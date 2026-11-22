"""Persistent document collaboration services and permission-aware API helpers."""
import json
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import auth, config, db

router = APIRouter(prefix="/api/collaboration", tags=["collaboration"])
_connections = set()          # open chat sockets: (websocket, application_id, session_token)
_code_failures = {}           # invitation id -> wrong verification-code attempts (per process)
MAX_CODE_ATTEMPTS = 5
GUEST_PERMISSIONS = {"view", "chat", "comment", "upload"}
# The server decides a file's type from its extension. The type the browser claims is not trusted:
# browsers often send application/octet-stream for PDFs, and a client could claim text/html.
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain; charset=utf-8",
}
SCOPE_OPTIONS = (
    "Specific Aims",
    "Research Strategy",
    "Methodology / Research Approach",
    "Scope of Work",
    "Facilities & Resources",
    "Subaward Budget",
    "Budget Justification",
    "Effort Commitment",
    "Equipment & Supplies",
    "Travel Expenses",
    "Biosketch / CV",
    "Current & Pending Support",
    "Personnel Information",
    "Letter of Support",
    "Letter of Commitment",
    "Signature / Institutional Approval",
    "Other",
)


def current_user(request: Request):
    from .main import current_user as main_current_user
    return main_current_user(request)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _storage_path(storage_key):
    root = Path(config.STORAGE_DIR).resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid storage key")
    return path


def _file_type(name):
    return FILE_TYPES.get(Path(name or "").suffix.lower(), "application/octet-stream")


async def _read_upload(file):
    """Check an uploaded file and return (suffix, bytes, content_type). Used by every upload path."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(415, "File type is not allowed")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is too large")
    if not content:
        raise HTTPException(400, "The file is empty")
    if suffix == ".pdf" and b"%PDF-" not in content[:1024]:
        raise HTTPException(400, "This file is named .pdf but is not a PDF document")
    return suffix, content, _file_type(suffix)


def _user_can_access(con, user, application_id):
    app = con.execute(
        "SELECT * FROM applications WHERE id=?", (application_id,)
    ).fetchone()
    if not app:
        raise HTTPException(404, "Application not found")
    if user.get("role") == "guest":
        if user.get("session_application_id") != application_id:
            raise HTTPException(403, "This guest session is limited to another application")
        return app
    if user["role"] == "sps":
        return app
    if user["id"] == app["owner_user_id"]:
        return app
    allowed = con.execute(
        """SELECT 1 FROM document_collaborators dc
           JOIN documents d ON d.id=dc.document_id
           WHERE d.application_id=? AND dc.user_id=?""",
        (application_id, user["id"]),
    ).fetchone()
    if not allowed:
        raise HTTPException(403, "You do not have access to this application")
    return app


def _can(assignment, need):
    """Does a document assignment allow 'comment' or 'edit'? Older rows only have the permission text."""
    if not assignment:
        return False
    if need == "comment":
        return bool(assignment["can_comment"]) or assignment["permission"] == "comment"
    if need == "edit":
        return bool(assignment["can_edit"]) or assignment["permission"] in ("edit", "upload")
    return True


def _document_access(con, user, document_id, need=None):
    """Who may see or change a document. need=None (view), 'comment' or 'edit'.

    The application owner and SPS have full access. Everyone else needs an assignment on the document.
    Note: documents.owner_user_id is the UPLOADER; access follows the APPLICATION owner
    (application_owner_id), because SPS or a collaborator may upload into a PI's application.
    """
    row = con.execute(
        """SELECT d.*, a.owner_user_id AS application_owner_id, a.title AS application_title,
                  u.display_name AS owner_name
           FROM documents d JOIN applications a ON a.id=d.application_id
           LEFT JOIN users u ON u.id=d.owner_user_id
           WHERE d.id=?""",
        (document_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    if user.get("role") == "guest" and user.get("session_application_id") != row["application_id"]:
        raise HTTPException(403, "This guest session is limited to another application")
    if user["role"] == "sps" or user["id"] == row["application_owner_id"]:
        return row
    assignment = con.execute(
        "SELECT * FROM document_collaborators WHERE document_id=? AND user_id=?",
        (document_id, user["id"]),
    ).fetchone()
    # Guests may view every document of the application they were invited to; others need an assignment.
    if not assignment and user.get("role") != "guest":
        raise HTTPException(403, "You do not have access to this document")
    if need and not _can(assignment, need):
        raise HTTPException(403, "You do not have permission to "
                            + ("comment on" if need == "comment" else "change") + " this document")
    return row


def _activity(con, user_id, application_id, document_id, event_type, metadata=None):
    con.execute(
        """INSERT INTO activity_logs
           (user_id,application_id,document_id,event_type,metadata,created_at)
           VALUES(?,?,?,?,?,?)""",
        (user_id, application_id, document_id, event_type,
         json.dumps(metadata or {}, separators=(",", ":")), _now()),
    )


def _notify(con, user_id, event_type, message):
    con.execute(
        "INSERT INTO notifications(user_id,event_type,message,created_at) VALUES(?,?,?,?)",
        (user_id, event_type, message, _now()),
    )


def seed_sample_data():
    """Demo data: SAMPLE-001/003 belong to pi.demo1, SAMPLE-002 to pi.demo2 (matches SAMPLE in main.py). Adds only what is missing."""
    samples = (("SAMPLE-001", "pi.demo1", "NIH sample application", "Budget justification", "Budget"),
               ("SAMPLE-002", "pi.demo2", "Foundation sample application", "Research plan", "Research plan"),
               ("SAMPLE-003", "pi.demo1", "NIH sample application (second)", "Letters of support", "Support"))
    with db.db() as con:
        now = _now()
        for app_id, login, title, doc_name, category in samples:
            owner = con.execute("SELECT id FROM users WHERE login_id=?", (login,)).fetchone()
            if not owner or con.execute("SELECT 1 FROM applications WHERE id=?", (app_id,)).fetchone():
                continue
            con.execute("INSERT INTO applications VALUES(?,?,?,?,?,?)", (app_id, owner["id"], title, "Incomplete", now, now))
            con.execute(
                """INSERT INTO documents
                   (application_id,name,category,owner_user_id,status,current_version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (app_id, doc_name, category, owner["id"], "Needs review", 0, now, now))


def init():
    seed_sample_data()


def _chat_message(row):
    message = {
        "id": row["id"], "application_id": row["application_id"],
        "author_name": row["author_name"], "author_role": row["author_role"],
        "body": "[Message deleted]" if row["deleted_at"] else row["body"],
        "edited": bool(row["edited_at"]), "deleted": bool(row["deleted_at"]),
        "created_at": row["created_at"],
    }
    if row["attachment_name"]:
        message["attachment"] = {
            "name": row["attachment_name"],
            "size_bytes": row["attachment_size_bytes"],
            "content_type": row["attachment_content_type"],
            "url": f"/api/collaboration/chat-attachments/{row['id']}/download",
        }
    return message


@router.get("/applications/{application_id}/chat")
def chat_history(application_id: str, user=Depends(current_user)):
    with db.db() as con:
        _user_can_access(con, user, application_id)
        rows = con.execute(
            """SELECT m.*, u.display_name AS author_name, u.role AS author_role
               FROM chat_messages m JOIN users u ON u.id=m.author_user_id
               WHERE m.application_id=? ORDER BY m.id DESC LIMIT 100""",
            (application_id,),
        ).fetchall()
    return [_chat_message(row) for row in reversed(rows)]


async def _broadcast(application_id, message):
    """Send a chat message to every open socket for this application whose session is still valid."""
    for entry in list(_connections):
        socket, socket_application_id, token = entry
        if socket_application_id != application_id:
            continue
        if not db.user_for_session(token):   # signed out or expired since connecting
            _connections.discard(entry)
            try:
                await socket.close(code=4401)
            except Exception:
                pass
            continue
        try:
            await socket.send_json(message)
        except Exception:
            _connections.discard(entry)


def _require_guest_permission(user, permission, message):
    if user["role"] == "guest" and permission not in user.get("guest_permissions", []):
        raise HTTPException(403, message)


@router.post("/applications/{application_id}/chat")
async def send_chat_message(application_id: str, body: str = Form(""),
                            file: UploadFile | None = File(None),
                            user=Depends(current_user)):
    _require_guest_permission(user, "chat", "You do not have permission to use the application chat")
    text = body.strip()
    if len(text) > 10000:
        raise HTTPException(400, "Message must contain 10000 characters or fewer")
    if file is not None and not file.filename:
        file = None   # an empty file field in a form
    if not text and not file:
        raise HTTPException(400, "Add a message or attach a file")
    attachment = None
    if file:
        _require_guest_permission(user, "upload", "You do not have permission to attach files")
        suffix, content, content_type = await _read_upload(file)
        attachment = (
            Path(file.filename or "attachment").name,
            f"chat-{secrets.token_hex(16)}{suffix}",
            content_type,
            len(content),
            content,
        )
    with db.db() as con:
        _user_can_access(con, user, application_id)
        now = _now()
        if attachment:
            path = _storage_path(attachment[1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(attachment[4])
        cur = con.execute(
            """INSERT INTO chat_messages
               (application_id,author_user_id,body,attachment_name,attachment_storage_key,
                attachment_content_type,attachment_size_bytes,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (application_id, user["id"], text,
             attachment[0] if attachment else None,
             attachment[1] if attachment else None,
             attachment[2] if attachment else None,
             attachment[3] if attachment else None, now),
        )
        row = con.execute(
            """SELECT m.*, u.display_name AS author_name, u.role AS author_role
               FROM chat_messages m JOIN users u ON u.id=m.author_user_id WHERE m.id=?""",
            (cur.lastrowid,),
        ).fetchone()
        _activity(con, user["id"], application_id, None, "chat_message_sent")
    message = _chat_message(row)
    await _broadcast(application_id, message)
    return message


@router.get("/chat-attachments/{message_id}/download")
def download_chat_attachment(message_id: int, user=Depends(current_user)):
    with db.db() as con:
        row = con.execute(
            """SELECT m.*, u.display_name AS author_name, u.role AS author_role
               FROM chat_messages m JOIN users u ON u.id=m.author_user_id WHERE m.id=?""",
            (message_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Chat message not found")
        _user_can_access(con, user, row["application_id"])
    if not row["attachment_storage_key"]:
        raise HTTPException(404, "Chat attachment not found")
    path = _storage_path(row["attachment_storage_key"])
    if not path.is_file():
        raise HTTPException(404, "Stored attachment is missing")
    return FileResponse(path, media_type=_file_type(row["attachment_storage_key"]),
                        filename=row["attachment_name"],
                        headers={"X-Content-Type-Options": "nosniff"})


@router.post("/documents/{document_id}/submit-review")
def submit_for_review(document_id: int, user=Depends(current_user)):
    with db.db() as con:
        doc = _document_access(con, user, document_id)
        if user["role"] == "guest" and not (
            {"comment", "upload"} & set(user.get("guest_permissions", []))
        ):
            raise HTTPException(403, "You do not have permission to send this document for review")
        con.execute(
            "UPDATE documents SET status='Ready for PI review',updated_at=? WHERE id=?",
            (_now(), document_id),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "document_sent_for_pi_review")
    return {"ok": True}


def _invitation(token):
    with db.db() as con:
        row = con.execute(
            "SELECT * FROM guest_invitations WHERE token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    if not row or row["revoked_at"] or row["expires_at"] <= _now():
        raise HTTPException(400, "Invitation is invalid or expired")
    return row


@router.post("/invitations")
def create_invitation(
    application_id: str = Form(...),
    email: str = Form(...),
    display_name: str = Form(""),
    permissions: str = Form('["view","chat","comment"]'),
    user=Depends(current_user),
):
    if user["role"] not in ("pi", "sps"):
        raise HTTPException(403, "Only a PI or SPS user can invite collaborators")
    try:
        parsed = json.loads(permissions)
        selected = sorted(set(parsed if isinstance(parsed, list) else []))
    except (TypeError, json.JSONDecodeError):
        selected = sorted({item.strip() for item in permissions.split(",") if item.strip()})
    if not selected:
        raise HTTPException(400, "Permissions must be a JSON array or comma-separated list")
    if not selected or not set(selected).issubset(GUEST_PERMISSIONS):
        raise HTTPException(400, "Invalid guest permissions")
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise HTTPException(400, "A valid collaborator email is required")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=config.GUEST_INVITATION_HOURS)).isoformat(timespec="seconds")
    with db.db() as con:
        app = _user_can_access(con, user, application_id)
        con.execute(
            """INSERT INTO guest_invitations
               (token_hash,application_id,invited_by,email,display_name,permissions,expires_at,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (hashlib.sha256(token.encode()).hexdigest(), application_id, user["id"], email,
             display_name.strip()[:200], json.dumps(selected), expires, _now()),
        )
        _activity(con, user["id"], application_id, None, "guest_invited",
                  {"email": email, "permissions": selected})
    return {
        "ok": True,
        "application_id": application_id,
        "invitation_url": f"{config.PUBLIC_URL}/auth/guest?token={token}",
        "expires_at": expires,
        "email_delivery": "not_configured",
    }


@router.post("/guest/request-code")
async def request_guest_code(token: str = Form(...)):
    invitation = _invitation(token)
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_expires = (datetime.now(timezone.utc) + timedelta(minutes=config.GUEST_CODE_MINUTES)).isoformat(timespec="seconds")
    with db.db() as con:
        con.execute(
            "UPDATE guest_invitations SET code_hash=?,code_expires_at=? WHERE id=?",
            (hashlib.sha256(code.encode()).hexdigest(), code_expires, invitation["id"]),
        )
    _code_failures.pop(invitation["id"], None)
    try:
        delivered = await auth.send_guest_code(
            invitation["email"], code, config.GUEST_CODE_MINUTES
        )
    except auth.AuthError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "message": "A verification code was sent to the invitation email.",
            "development_code": code if not delivered else None}


@router.post("/guest/verify")
def verify_guest(
    token: str = Form(...),
    code: str = Form(...),
    display_name: str = Form(""),
):
    invitation = _invitation(token)
    if not invitation["code_hash"] or not invitation["code_expires_at"] or invitation["code_expires_at"] <= _now():
        raise HTTPException(400, "Request a new verification code")
    if not secrets.compare_digest(
        invitation["code_hash"], hashlib.sha256(code.strip().encode()).hexdigest()
    ):
        # Six digits can be guessed by brute force, so a code stops working after a few wrong tries.
        failures = _code_failures.get(invitation["id"], 0) + 1
        _code_failures[invitation["id"]] = failures
        if failures >= MAX_CODE_ATTEMPTS:
            _code_failures.pop(invitation["id"], None)
            with db.db() as con:
                con.execute("UPDATE guest_invitations SET code_hash=NULL, code_expires_at=NULL WHERE id=?",
                            (invitation["id"],))
            raise HTTPException(401, "Invalid verification code. Too many attempts: request a new code.")
        raise HTTPException(401, "Invalid verification code")
    _code_failures.pop(invitation["id"], None)
    name = display_name.strip()[:200] or invitation["display_name"] or invitation["email"]
    with db.db() as con:
        now = _now()
        # Reuse the guest account for this email, so earlier document assignments and tasks still apply
        # when the guest verifies again (new browser, expired session).
        existing = con.execute(
            "SELECT id FROM users WHERE role='guest' AND email=? ORDER BY id DESC LIMIT 1",
            (invitation["email"],),
        ).fetchone()
        if existing:
            user_id = existing["id"]
            con.execute("UPDATE users SET active=1, last_login_at=?, "
                        "display_name=COALESCE(NULLIF(?,''), display_name) WHERE id=?",
                        (now, display_name.strip()[:200], user_id))
        else:
            cur = con.execute(
                """INSERT INTO users(login_id,email,display_name,role,active,created_at,last_login_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (f"guest-{secrets.token_hex(12)}", invitation["email"], name, "guest", 1, now, now),
            )
            user_id = cur.lastrowid
        con.execute("UPDATE guest_invitations SET verified_at=?, code_hash=NULL WHERE id=?",
                    (now, invitation["id"]))
        _activity(con, user_id, invitation["application_id"], None, "guest_verified",
                  {"email": invitation["email"]})
    session = db.create_guest_session(user_id, invitation["application_id"])
    from fastapi.responses import JSONResponse
    response = JSONResponse({"ok": True, "application_id": invitation["application_id"]})
    response.set_cookie("session", session, httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE, max_age=config.SESSION_HOURS * 3600)
    return response


@router.get("/applications")
def applications(user=Depends(current_user)):
    """Return only applications the signed-in user is allowed to see."""
    with db.db() as con:
        base = """SELECT a.*, u.display_name AS owner_name, u.login_id AS owner_login_id,
                         (SELECT COUNT(*) FROM documents d WHERE d.application_id=a.id) AS document_count
                  FROM applications a JOIN users u ON u.id=a.owner_user_id"""
        if user["role"] == "guest":
            rows = con.execute(
                base + " WHERE a.id=? ORDER BY a.updated_at DESC",
                (user["session_application_id"],),
            ).fetchall()
        elif user["role"] == "sps":
            rows = con.execute(base + " ORDER BY a.updated_at DESC").fetchall()
        else:
            rows = con.execute(
                base + " WHERE a.owner_user_id=? ORDER BY a.updated_at DESC",
                (user["id"],),
            ).fetchall()
    return [dict(row) for row in rows]


@router.post("/applications/{application_id}/status")
def update_application_status(
    application_id: str,
    status: str = Form(...),
    user=Depends(current_user),
):
    """Allow SPS to update the review status of an application."""
    if user["role"] != "sps":
        raise HTTPException(403, "Only SPS can update application review status")
    allowed = {"Incomplete", "In review by SPS", "Approved", "Changes requested"}
    if status not in allowed:
        raise HTTPException(400, "Invalid application status")
    with db.db() as con:
        app = con.execute("SELECT id FROM applications WHERE id=?", (application_id,)).fetchone()
        if not app:
            raise HTTPException(404, "Application not found")
        con.execute(
            "UPDATE applications SET status=?, updated_at=? WHERE id=?",
            (status, _now(), application_id),
        )
        _activity(con, user["id"], application_id, None, "application_status_updated",
                  {"status": status})
    return {"ok": True, "id": application_id, "status": status}


@router.get("/documents")
def documents(application_id: str | None = None, user=Depends(current_user)):
    with db.db() as con:
        if application_id:
            _user_can_access(con, user, application_id)
            rows = con.execute(
                """SELECT d.*, u.display_name AS owner_name,
                          a.title AS application_title
                   FROM documents d JOIN users u ON u.id=d.owner_user_id
                   JOIN applications a ON a.id=d.application_id
                   WHERE d.application_id=? ORDER BY d.updated_at DESC""",
                (application_id,),
            ).fetchall()
        elif user["role"] == "sps":
            rows = con.execute(
                """SELECT d.*, u.display_name AS owner_name, a.title AS application_title
                   FROM documents d JOIN users u ON u.id=d.owner_user_id
                   JOIN applications a ON a.id=d.application_id ORDER BY d.updated_at DESC"""
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT DISTINCT d.*, u.display_name AS owner_name, a.title AS application_title
                   FROM documents d JOIN users u ON u.id=d.owner_user_id
                   JOIN applications a ON a.id=d.application_id
                   LEFT JOIN document_collaborators dc ON dc.document_id=d.id
                   WHERE a.owner_user_id=? OR dc.user_id=? ORDER BY d.updated_at DESC""",
                (user["id"], user["id"]),
            ).fetchall()
    return [dict(row) for row in rows]


@router.post("/documents")
async def create_document(
    application_id: str = Form(...),
    name: str = Form(...),
    category: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(current_user),
):
    clean_name = Path(name.strip()).name
    if not clean_name or len(clean_name) > 200:
        raise HTTPException(400, "Document name is required and must be 200 characters or fewer")
    suffix, content, content_type = await _read_upload(file)
    with db.db() as con:
        app = _user_can_access(con, user, application_id)
        if user["role"] not in ("pi", "sps") and user["id"] != app["owner_user_id"]:
            raise HTTPException(403, "You cannot create documents for this application")
        now = _now()
        cur = con.execute(
            """INSERT INTO documents
               (application_id,name,category,owner_user_id,status,current_version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (application_id, clean_name, category.strip()[:80] or "Other",
             user["id"], "Uploaded", 0, now, now),
        )
        document_id = cur.lastrowid
        storage_key = f"{secrets.token_hex(16)}{suffix}"
        path = _storage_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        con.execute(
            """INSERT INTO document_versions
               (document_id,version,original_name,storage_key,content_type,size_bytes,uploaded_by,uploaded_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (document_id, 1, Path(file.filename or clean_name).name, storage_key,
             content_type, len(content), user["id"], now),
        )
        con.execute("UPDATE documents SET current_version=1 WHERE id=?", (document_id,))
        _activity(con, user["id"], application_id, document_id, "document_created",
                  {"name": clean_name, "category": category})
    return {"id": document_id, "application_id": application_id, "version": 1}


@router.get("/documents/{document_id}")
def document(document_id: int, user=Depends(current_user)):
    with db.db() as con:
        row = _document_access(con, user, document_id)
        versions = con.execute(
            """SELECT v.*, u.display_name AS uploader_name
               FROM document_versions v JOIN users u ON u.id=v.uploaded_by
               WHERE v.document_id=? ORDER BY v.version DESC""",
            (document_id,),
        ).fetchall()
        comments = con.execute(
            """SELECT c.*, u.display_name AS author_name, u.role AS author_role
               FROM comments c JOIN users u ON u.id=c.author_user_id
               WHERE c.document_id=? ORDER BY c.created_at ASC""",
            (document_id,),
        ).fetchall()
        activity = con.execute(
            """SELECT l.*, u.display_name AS actor_name
               FROM activity_logs l LEFT JOIN users u ON u.id=l.user_id
               WHERE l.document_id=? ORDER BY l.created_at DESC""",
            (document_id,),
        ).fetchall()
        assignments = con.execute(
            """SELECT dc.*, u.display_name AS collaborator_name, u.email AS collaborator_email,
                      u.role AS collaborator_role
               FROM document_collaborators dc JOIN users u ON u.id=dc.user_id
               WHERE dc.document_id=? ORDER BY u.display_name""",
            (document_id,),
        ).fetchall()
    return {**dict(row), "versions": [dict(v) for v in versions],
            "comments": [dict(c) for c in comments],
            "activity": [dict(a) for a in activity],
            "assignments": [dict(a) for a in assignments],
            "scope_options": list(SCOPE_OPTIONS)}


@router.post("/documents/{document_id}/versions")
async def upload_version(
    document_id: int,
    file: UploadFile = File(...),
    user=Depends(current_user),
):
    suffix, content, content_type = await _read_upload(file)
    with db.db() as con:
        _require_guest_permission(user, "upload", "You do not have upload permission")
        doc = _document_access(con, user, document_id, need="edit")
        version = doc["current_version"] + 1
        storage_key = f"{secrets.token_hex(16)}{suffix}"
        path = _storage_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        now = _now()
        con.execute(
            """INSERT INTO document_versions
               (document_id,version,original_name,storage_key,content_type,size_bytes,uploaded_by,uploaded_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (document_id, version, Path(file.filename or "upload").name, storage_key,
             content_type, len(content), user["id"], now),
        )
        con.execute(
            "UPDATE documents SET current_version=?,status='Uploaded',updated_at=? WHERE id=?",
            (version, now, document_id),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "version_uploaded",
                  {"version": version, "name": Path(file.filename or "upload").name})
    return {"ok": True, "document_id": document_id, "version": version}


@router.get("/documents/{document_id}/versions/{version}/download")
def download_version(document_id: int, version: int, user=Depends(current_user)):
    with db.db() as con:
        _document_access(con, user, document_id)
        row = con.execute(
            "SELECT * FROM document_versions WHERE document_id=? AND version=?",
            (document_id, version),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Version not found")
    path = _storage_path(row["storage_key"])
    if not path.is_file():
        raise HTTPException(404, "Stored file is missing")
    return FileResponse(path, media_type=_file_type(row["storage_key"]), filename=row["original_name"],
                        headers={"X-Content-Type-Options": "nosniff"})


@router.get("/documents/{document_id}/versions/{version}/preview")
def preview_version(document_id: int, version: int, user=Depends(current_user)):
    with db.db() as con:
        _document_access(con, user, document_id)
        row = con.execute(
            "SELECT * FROM document_versions WHERE document_id=? AND version=?",
            (document_id, version),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Version not found")
    if _file_type(row["storage_key"]) != "application/pdf":
        raise HTTPException(415, "Only PDF files can be previewed")
    path = _storage_path(row["storage_key"])
    if not path.is_file():
        raise HTTPException(404, "Stored file is missing")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=None,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/documents/{document_id}/comments")
def add_comment(document_id: int, body: str = Form(...), parent_id: int | None = Form(None),
                scope: str = Form(""), user=Depends(current_user)):
    text = body.strip()
    if not text or len(text) > 10000:
        raise HTTPException(400, "Comment must contain 1-10000 characters")
    with db.db() as con:
        _require_guest_permission(user, "comment", "You do not have comment permission")
        doc = _document_access(con, user, document_id, need="comment")
        if parent_id is not None and not con.execute(
                "SELECT 1 FROM comments WHERE id=? AND document_id=?", (parent_id, document_id)).fetchone():
            raise HTTPException(400, "The comment you are replying to is not on this document")
        con.execute(
            "INSERT INTO comments(document_id,parent_id,author_user_id,body,scope,created_at) VALUES(?,?,?,?,?,?)",
            (document_id, parent_id, user["id"], text, scope.strip()[:120], _now()),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "comment_added")
        if doc["application_owner_id"] != user["id"]:
            _notify(con, doc["application_owner_id"], "comment_added",
                    f"New comment on {doc['name']} ({doc['application_id']})")
    return {"ok": True}


@router.post("/documents/{document_id}/comments/{comment_id}/resolve")
def resolve_comment(document_id: int, comment_id: int, user=Depends(current_user)):
    with db.db() as con:
        doc = _document_access(con, user, document_id)
        if user["id"] != doc["application_owner_id"]:
            raise HTTPException(403, "Only the application's PI can resolve comments")
        result = con.execute(
            "UPDATE comments SET resolved_at=?,resolved_by=? WHERE id=? AND document_id=? AND resolved_at IS NULL",
            (_now(), user["id"], comment_id, document_id),
        )
        if not result.rowcount:
            raise HTTPException(404, "Open comment not found")
        _activity(con, user["id"], doc["application_id"], document_id, "comment_resolved",
                  {"comment_id": comment_id})
    return {"ok": True}


@router.get("/activity")
def activity(application_id: str | None = None, user=Depends(current_user)):
    with db.db() as con:
        if application_id:
            _user_can_access(con, user, application_id)
            rows = con.execute(
                """SELECT l.*, u.display_name AS actor_name FROM activity_logs l
                   LEFT JOIN users u ON u.id=l.user_id
                   WHERE l.application_id=? ORDER BY l.created_at DESC""",
                (application_id,),
            ).fetchall()
        else:
            rows = []
    return [dict(row) for row in rows]


@router.get("/notifications")
def notifications(user=Depends(current_user)):
    with db.db() as con:
        rows = con.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user=Depends(current_user)):
    with db.db() as con:
        result = con.execute(
            "UPDATE notifications SET read_at=? WHERE id=? AND user_id=?",
            (_now(), notification_id, user["id"]),
        )
        if not result.rowcount:
            raise HTTPException(404, "Notification not found")
    return {"ok": True}


@router.get("/tasks")
def tasks(application_id: str | None = None, user=Depends(current_user)):
    with db.db() as con:
        if application_id:
            _user_can_access(con, user, application_id)
            rows = con.execute(
                """SELECT t.*, a.title, d.name AS document_name,
                          assignee.display_name AS assignee_name,
                          creator.display_name AS creator_name
                   FROM tasks t JOIN applications a ON a.id=t.application_id
                   LEFT JOIN documents d ON d.id=t.document_id
                   JOIN users assignee ON assignee.id=t.assigned_to
                   JOIN users creator ON creator.id=t.created_by
                   WHERE t.application_id=? ORDER BY t.created_at DESC""",
                (application_id,),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT t.*, a.title, d.name AS document_name,
                          assignee.display_name AS assignee_name,
                          creator.display_name AS creator_name
                   FROM tasks t JOIN applications a ON a.id=t.application_id
                   LEFT JOIN documents d ON d.id=t.document_id
                   JOIN users assignee ON assignee.id=t.assigned_to
                   JOIN users creator ON creator.id=t.created_by
                   WHERE t.assigned_to=? OR t.created_by=? ORDER BY t.created_at DESC""",
                (user["id"], user["id"]),
            ).fetchall()
    return [dict(row) for row in rows]


@router.post("/tasks")
def create_task(
    application_id: str = Form(...),
    description: str = Form(...),
    assigned_to: int = Form(...),
    document_id: int | None = Form(None),
    due_at: str | None = Form(None),
    priority: str = Form("Normal"),
    user=Depends(current_user),
):
    with db.db() as con:
        app = _user_can_access(con, user, application_id)
        if user["role"] not in ("pi", "sps"):
            raise HTTPException(403, "Only PI or SPS users may assign tasks")
        target = con.execute(
            "SELECT id FROM users WHERE id=? AND active=1", (assigned_to,)
        ).fetchone()
        if not target:
            raise HTTPException(400, "Assigned user is not active")
        if document_id:
            if _document_access(con, user, document_id)["application_id"] != application_id:
                raise HTTPException(400, "That document belongs to a different application")
        cur = con.execute(
            """INSERT INTO tasks(application_id,document_id,description,assigned_to,created_by,due_at,priority,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (application_id, document_id, description.strip(), assigned_to, user["id"],
             due_at, priority, _now()),
        )
        _activity(con, user["id"], application_id, document_id, "task_assigned",
                  {"task_id": cur.lastrowid, "assigned_to": assigned_to})
        _notify(con, assigned_to, "task_assigned", "You have been assigned a collaboration task")
        return {"id": cur.lastrowid}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, user=Depends(current_user)):
    with db.db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        if user["id"] != task["assigned_to"] and user["role"] != "sps":
            raise HTTPException(403, "Only the assignee or SPS may complete this task")
        con.execute(
            "UPDATE tasks SET status='Completed',completed_at=? WHERE id=?",
            (_now(), task_id),
        )
        _activity(con, user["id"], task["application_id"], task["document_id"], "task_completed",
                  {"task_id": task_id})
    return {"ok": True}


@router.post("/documents/{document_id}/collaborators")
def share_document(
    document_id: int,
    user_id: int = Form(...),
    scope: str = Form(""),
    can_comment: bool = Form(False),
    can_edit: bool = Form(False),
    due_at: str | None = Form(None),
    user=Depends(current_user),
):
    due_at = (due_at or "").strip() or None   # an empty deadline field means "no deadline"
    if scope and scope not in SCOPE_OPTIONS:
        raise HTTPException(400, "Invalid document scope")
    with db.db() as con:
        doc = _document_access(con, user, document_id)
        if user["role"] != "sps" and user["id"] != doc["application_owner_id"]:
            raise HTTPException(403, "Only the application's PI or SPS can assign contributors")
        target = con.execute("SELECT id FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        if not target:
            raise HTTPException(400, "Collaborator is not active")
        permission = "edit" if can_edit else ("comment" if can_comment else "view")
        con.execute(
            """INSERT INTO document_collaborators
               (document_id,user_id,permission,scope,can_comment,can_edit,due_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(document_id,user_id) DO UPDATE SET
               permission=excluded.permission,scope=excluded.scope,
               can_comment=excluded.can_comment,can_edit=excluded.can_edit,due_at=excluded.due_at""",
            (document_id, user_id, permission, scope, int(can_comment), int(can_edit), due_at),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "contributor_invited",
                  {"user_id": user_id, "permission": permission, "scope": scope, "due_at": due_at})
        _notify(con, user_id, "contributor_invited", "A document was shared with you")
        if due_at:
            con.execute(
                """INSERT INTO tasks(application_id,document_id,description,assigned_to,created_by,due_at,priority,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (doc["application_id"], document_id,
                 f"{scope or 'Document'} contribution: {doc['name']}",
                 user_id, user["id"], due_at, "Normal", "Pending", _now()),
            )
    return {"ok": True}


@router.get("/application-guests")
def application_guests(application_id: str, user=Depends(current_user)):
    with db.db() as con:
        _user_can_access(con, user, application_id)
        if user["role"] not in ("pi", "sps"):
            raise HTTPException(403, "Only PI or SPS users can assign collaborators")
        # Based on verified invitations, so guests stay listed after they sign out or their session expires.
        # One row per email (older data may hold several guest accounts for one email; take the newest).
        rows = con.execute(
            """SELECT MAX(u.id) AS id, u.display_name, u.email, u.role
               FROM users u JOIN guest_invitations gi ON gi.email=u.email
               WHERE u.role='guest' AND u.active=1 AND gi.application_id=?
                 AND gi.verified_at IS NOT NULL AND gi.revoked_at IS NULL
               GROUP BY u.email ORDER BY u.display_name""",
            (application_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.websocket("/ws/{application_id}")
async def collaboration_socket(websocket: WebSocket, application_id: str):
    """Live application chat. The same permission rules as the HTTP chat endpoint apply,
    and the session is re-checked for every message, so signing out also stops an open socket."""
    token = websocket.cookies.get("session")
    user = db.user_for_session(token) if token else None
    if not user:
        await websocket.close(code=4401)
        return
    if user["role"] == "guest" and "chat" not in db.guest_permissions(
        user["id"], user["session_application_id"]
    ):
        await websocket.close(code=4403)
        return
    try:
        with db.db() as con:
            _user_can_access(con, user, application_id)
    except HTTPException:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    entry = (websocket, application_id, token)
    _connections.add(entry)
    try:
        while True:
            raw = await websocket.receive_text()
            if not db.user_for_session(token):
                await websocket.close(code=4401)
                break
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            text = str(payload.get("body", "")).strip()
            if not text or len(text) > 10000:
                continue
            with db.db() as con:
                cur = con.execute(
                    "INSERT INTO chat_messages(application_id,author_user_id,body,created_at) VALUES(?,?,?,?)",
                    (application_id, user["id"], text, _now()),
                )
                row = con.execute(
                    """SELECT m.*, u.display_name AS author_name, u.role AS author_role
                       FROM chat_messages m JOIN users u ON u.id=m.author_user_id WHERE m.id=?""",
                    (cur.lastrowid,),
                ).fetchone()
                _activity(con, user["id"], application_id, None, "chat_message_sent")
            await _broadcast(application_id, _chat_message(row))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(entry)
