"""The backend. Run it from the backend/ folder:  uvicorn app.main:app --reload --port 8000"""
from contextlib import asynccontextmanager
from html import escape

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
    entrance = request.session.pop("as", "pi")
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
def demo_page():
    if config.AUTH_PROVIDER != "demo":
        raise HTTPException(404)
    with db.db() as con:
        rows = con.execute("SELECT login_id, display_name, role FROM users WHERE active=1 ORDER BY role, login_id").fetchall()
    items = "".join(f'<li><a href="/auth/demo/login?login_id={escape(r["login_id"])}">{escape(r["display_name"])}</a> ({r["role"]})</li>' for r in rows)
    return f"<h1>Demo sign-in (local development only)</h1><p>Pick a person. Real UConn sign-in replaces this page.</p><ul>{items}</ul>"


@app.get("/auth/demo/login")
def demo_login(request: Request, login_id: str):
    if config.AUTH_PROVIDER != "demo":
        raise HTTPException(404)
    return finish(request, {"login_id": login_id.lower(), "email": ""}, "demo")


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
    return {"login_id": user["login_id"], "name": user["display_name"], "email": user["email"], "role": user["role"]}


SAMPLE = [{"id": "SAMPLE-001", "owner": "pi.demo1", "status": "Incomplete"},
          {"id": "SAMPLE-002", "owner": "pi.demo2", "status": "In review by SPS"},
          {"id": "SAMPLE-003", "owner": "pi.demo1", "status": "Incomplete"}]


@app.get("/api/requests")
def requests_list(user=Depends(current_user)):
    """A PI only ever gets their own requests. SPS gets all of them. The page cannot change this."""
    return SAMPLE if user["role"] == "sps" else [r for r in SAMPLE if r["owner"] == user["login_id"]]


@app.get("/api/staff/overview")
def staff_overview(user=Depends(require_role("sps"))):
    return {"total": len(SAMPLE), "in_review": sum(r["status"] == "In review by SPS" for r in SAMPLE)}


@app.get("/api/health")
def health():
    return {"ok": True}


from .collaboration import router as collaboration_router

app.include_router(collaboration_router)

from .assistant import router as assistant_router  # the AI Judge chat (signed-in users only)

app.include_router(assistant_router)


if Path(config.FRONTEND_DIR).is_dir():  # last, so it never hides the routes above
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
