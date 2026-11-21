"""The backend. Run it from the repo root:  uvicorn backend.main:app --reload --port 8000"""
from contextlib import asynccontextmanager
from html import escape
import json

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db


@asynccontextmanager
async def lifespan(app):
    if config.AUTH_PROVIDER == "demo":
        if not config.ALLOW_DEMO:
            raise RuntimeError("Demo sign-in is switched off (ALLOW_DEMO=0). Set AUTH_PROVIDER to cas or oidc.")
        print("WARNING: demo sign-in is ON. Anyone can pick any user. Local development only.")
    else:
        if config.SESSION_SECRET == "dev-only-change-me":
            raise RuntimeError("Set a real SESSION_SECRET before using cas or oidc.")
        if config.AUTH_PROVIDER == "cas" and not config.CAS_SERVER_URL:
            raise RuntimeError("Set CAS_SERVER_URL (UConn IT gives you this).")
        if config.AUTH_PROVIDER == "oidc":
            if not (config.OIDC_METADATA_URL and config.OIDC_CLIENT_ID and config.OIDC_CLIENT_SECRET):
                raise RuntimeError("Set OIDC_METADATA_URL, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET (UConn IT gives you these).")
            auth.setup_oidc()
    db.init_db()
    from .collaboration import init as init_collaboration
    init_collaboration()
    yield


app = FastAPI(title="Grant Assistant", lifespan=lifespan)
# A short-lived cookie that only remembers small notes DURING sign-in (which entrance was used, the OIDC state).
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="login_state",
                   max_age=900, same_site="lax", https_only=config.COOKIE_SECURE)


# ---------- who is calling? ----------
def current_user(request: Request):
    token = request.cookies.get("session")
    user = db.user_for_session(token) if token else None
    if not user:
        raise HTTPException(401, "Not signed in")
    if user["role"] == "guest":
        user["guest_permissions"] = db.guest_permissions(user["id"], user["session_application_id"])
    return user


def require_role(*roles):
    """The door check. The backend enforces the role on every request. Hiding buttons in the page is only a convenience."""
    def check(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Not allowed for your role")
        return user
    return check


# ---------- sign-in ----------
def finish(request: Request, who, provider):
    """The provider said who this is. Now: are they on the guest list, and did they use the right entrance?"""
    # demo: keep the entrance so several sign-ins in a row behave; real providers still use it once
    entrance = request.session.get("as", "pi") if provider == "demo" else request.session.pop("as", "pi")
    wanted = "sps" if entrance == "staff" else "pi"
    user = db.find_user(who["login_id"], who.get("email"))
    if not user:
        db.log_event(provider, who["login_id"], False, "not on the allowed list")
        return RedirectResponse("/?login_error=not_allowed#login", 303)
    if user["role"] != wanted:
        db.log_event(provider, user["login_id"], False, f"{user['role']} account used the {entrance} entrance")
        return RedirectResponse("/?login_error=wrong_entrance#login", 303)
    token = db.create_session(user["id"])
    db.log_event(provider, user["login_id"], True)
    resp = RedirectResponse("/#" + ("staff" if user["role"] == "sps" else "pi"), 303)
    resp.set_cookie("session", token, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                    max_age=config.SESSION_HOURS * 3600)
    return resp


@app.get("/auth/login")
async def login(request: Request, entrance: str = Query("pi", alias="as")):
    request.session["as"] = entrance if entrance in ("pi", "staff") else "pi"
    if config.AUTH_PROVIDER == "cas":
        return RedirectResponse(auth.cas_login_url())
    if config.AUTH_PROVIDER == "oidc":
        return await auth.oidc_login(request)
    return RedirectResponse("/auth/demo")


@app.get("/auth/callback")
async def callback(request: Request, ticket: str | None = None):
    try:
        if config.AUTH_PROVIDER == "cas":
            if not ticket:
                raise auth.AuthError("no ticket came back from CAS")
            who = await auth.cas_identity(ticket)
        elif config.AUTH_PROVIDER == "oidc":
            who = await auth.oidc_identity(request)
        else:
            raise auth.AuthError("callback is not used in demo mode")
    except auth.AuthError as exc:
        db.log_event(config.AUTH_PROVIDER, None, False, str(exc))
        return RedirectResponse("/?login_error=failed#login", 303)
    return finish(request, who, config.AUTH_PROVIDER)


@app.get("/auth/demo", response_class=HTMLResponse)
def demo_page(request: Request):
    if config.AUTH_PROVIDER != "demo":
        raise HTTPException(404)
    wanted = "sps" if request.session.get("as") == "staff" else "pi"   # only offer users this entrance accepts
    with db.db() as con:
        rows = con.execute("SELECT login_id, display_name, role FROM users WHERE active=1 AND (role=? OR role='guest') ORDER BY role, login_id", (wanted,)).fetchall()
    items = "".join(f'<li><a href="/auth/demo/login?login_id={escape(r["login_id"])}">{escape(r["display_name"])}</a> ({r["role"]})</li>' for r in rows)
    return f"<h1>Demo sign-in (local development only)</h1><p>PI and SPS users use institutional-style demo cards. Guest collaborators use their verified guest card below.</p><ul>{items}</ul>"


@app.get("/auth/demo/login")
def demo_login(request: Request, login_id: str):
    if config.AUTH_PROVIDER != "demo":
        raise HTTPException(404)
    user = db.find_user(login_id.lower())
    if not user:
        raise HTTPException(404)
    if user["role"] == "guest":
        with db.db() as con:
            invitation = con.execute(
                """SELECT application_id FROM guest_invitations
                   WHERE email=? AND verified_at IS NOT NULL AND revoked_at IS NULL
                   ORDER BY verified_at DESC LIMIT 1""",
                (user["email"],),
            ).fetchone()
        if not invitation:
            raise HTTPException(403, "This guest account has no active application invitation")
        token = db.create_guest_session(user["id"], invitation["application_id"])
        db.log_event("demo", user["login_id"], True, "demo guest collaborator")
        resp = RedirectResponse("/#guest", 303)
        resp.set_cookie("session", token, httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE,
                        max_age=config.SESSION_HOURS * 3600)
        return resp
    return finish(request, {"login_id": login_id.lower(), "email": ""}, "demo")


@app.get("/auth/guest", response_class=HTMLResponse)
def guest_login():
    return HTMLResponse("""<!doctype html><title>Guest invitation</title>
    <style>body{font-family:system-ui;margin:40px;max-width:560px} #status{padding:12px;border:1px solid #d5dae1;border-radius:8px} #status code{display:block;font-size:28px;font-weight:700;margin-top:6px;letter-spacing:4px} form{display:flex;flex-direction:column;gap:12px;margin:20px 0} input,button{padding:10px;font:inherit}</style>
    <h1>Guest invitation</h1><p id="status">Requesting your local demo verification code...</p>
    <form id="guest"><input type="hidden" name="token"><label>Name (optional)
    <input name="display_name" maxlength="200"></label><label>Verification code
    <input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required></label>
    <button>Verify and continue</button></form>
    <button id="send" type="button">Send a new code</button>
    <script>
    const token=new URLSearchParams(location.search).get('token')||'';
    document.querySelector('[name=token]').value=token;
    async function send(){const f=new FormData();f.append('token',token);
      const r=await fetch('/api/collaboration/guest/request-code',{method:'POST',body:f});
      const d=await r.json();const status=document.querySelector('#status');
      if(d.development_code){status.innerHTML='Local demo verification code:<code>'+d.development_code+'</code>Enter this code below.';
      const code=document.querySelector('[name=code]');code.value=d.development_code;code.focus();}
      else status.textContent=d.message||d.detail||'Unable to send code';}
    document.querySelector('#send').onclick=send;
    document.querySelector('#guest').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/collaboration/guest/verify',{method:'POST',body:new FormData(e.target)});
      if(r.ok){const d=await r.json();location.href='/#guest';}else document.querySelector('#status').textContent=(await r.json()).detail||'Verification failed';};
    if(token)send();else document.querySelector('#status').textContent='Open the complete invitation link from your email.';
    </script>""")


@app.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        db.end_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


# ---------- API (every one of these checks who you are) ----------
@app.get("/api/me")
def me(user=Depends(current_user)):
    result = {"id": user["id"], "login_id": user["login_id"], "name": user["display_name"],
              "email": user["email"], "role": user["role"]}
    if user["role"] == "guest":
        result["guest_permissions"] = user.get("guest_permissions", [])
        result["application_id"] = user.get("session_application_id")
    return result


@app.get("/api/requests")
def requests_list(user=Depends(current_user)):
    """Return real application records, scoped to the signed-in user's permissions."""
    with db.db() as con:
        base = """SELECT a.id, a.title, a.status, a.owner_user_id, a.created_at, a.updated_at,
                         u.login_id AS owner_login_id, u.display_name AS owner_name
                  FROM applications a JOIN users u ON u.id=a.owner_user_id"""
        if user["role"] == "sps":
            rows = con.execute(base + " ORDER BY a.updated_at DESC").fetchall()
        elif user["role"] == "guest":
            rows = con.execute(
                base + " WHERE a.id=? ORDER BY a.updated_at DESC",
                (user.get("session_application_id"),),
            ).fetchall()
        else:
            rows = con.execute(
                base + " WHERE a.owner_user_id=? ORDER BY a.updated_at DESC",
                (user["id"],),
            ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/staff/overview")
def staff_overview(user=Depends(require_role("sps"))):
    with db.db() as con:
        total = con.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        in_review = con.execute(
            "SELECT COUNT(*) FROM applications WHERE status='In review by SPS'"
        ).fetchone()[0]
    return {"total": total, "in_review": in_review}


@app.get("/api/health")
def health():
    return {"ok": True}


from .collaboration import router as collaboration_router

app.include_router(collaboration_router)


class FrontendFiles(StaticFiles):
    """Serve only page assets. Defense in depth: even if FRONTEND_DIR is ever pointed at a folder that also
    holds Python code, the allowed-users list or backups, anything that is not a web asset type gets a 404."""
    WEB_ASSETS = {".html", ".css", ".js", ".woff2", ".svg", ".png", ".ico", ".webp"}

    async def get_response(self, path, scope):
        if path not in ("", ".") and Path(path).suffix.lower() not in self.WEB_ASSETS:
            raise HTTPException(404)
        return await super().get_response(path, scope)


if Path(config.FRONTEND_DIR).is_dir():  # last, so it never hides the routes above
    app.mount("/", FrontendFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
