"""Persistent document collaboration services and permission-aware API helpers."""
import json
import mimetypes
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import config, db

router = APIRouter(prefix="/api/collaboration", tags=["collaboration"])
_connections = set()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _storage_path(storage_key):
    root = Path(config.STORAGE_DIR).resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid storage key")
    return path


def _user_can_access(con, user, application_id):
    app = con.execute(
        "SELECT * FROM applications WHERE id=?", (application_id,)
    ).fetchone()
    if not app:
        raise HTTPException(404, "Application not found")
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


def _document_access(con, user, document_id, write=False):
    row = con.execute(
        """SELECT d.*, a.owner_user_id, a.title AS application_title
           FROM documents d JOIN applications a ON a.id=d.application_id
           WHERE d.id=?""",
        (document_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    if user["role"] == "sps" or user["id"] == row["owner_user_id"]:
        return row
    collaborator = con.execute(
        "SELECT permission FROM document_collaborators WHERE document_id=? AND user_id=?",
        (document_id, user["id"]),
    ).fetchone()
    if not collaborator:
        raise HTTPException(403, "You do not have access to this document")
    if write and collaborator["permission"] not in ("upload", "comment"):
        raise HTTPException(403, "You do not have permission to change this document")
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
    with db.db() as con:
        pi = con.execute("SELECT id FROM users WHERE login_id='pi.demo1'").fetchone()
        sps = con.execute("SELECT id FROM users WHERE role='sps' LIMIT 1").fetchone()
        if not pi or not sps:
            return
        if con.execute("SELECT 1 FROM applications LIMIT 1").fetchone():
            return
        now = _now()
        con.execute(
            "INSERT INTO applications VALUES(?,?,?,?,?,?)",
            ("SAMPLE-001", pi["id"], "NIH sample application", "Incomplete", now, now),
        )
        con.execute(
            """INSERT INTO documents
               (application_id,name,category,owner_user_id,status,current_version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("SAMPLE-001", "Budget justification", "Budget", pi["id"], "Needs review", 0, now, now),
        )


def init():
    seed_sample_data()


@router.get("/applications")
def applications(user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
    with db.db() as con:
        if user["role"] == "sps":
            rows = con.execute("SELECT * FROM applications ORDER BY updated_at DESC").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM applications WHERE owner_user_id=? ORDER BY updated_at DESC",
                (user["id"],),
            ).fetchall()
    return [dict(row) for row in rows]


@router.get("/documents")
def documents(application_id: str | None = None, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
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
    user=Depends(__import__("files.main", fromlist=["current_user"]).current_user),
):
    clean_name = Path(name.strip()).name
    if not clean_name or len(clean_name) > 200:
        raise HTTPException(400, "Document name is required and must be 200 characters or fewer")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(415, "File type is not allowed")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is too large")
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
             file.content_type or mimetypes.guess_type(file.filename or "")[0],
             len(content), user["id"], now),
        )
        con.execute("UPDATE documents SET current_version=1 WHERE id=?", (document_id,))
        _activity(con, user["id"], application_id, document_id, "document_created",
                  {"name": clean_name, "category": category})
    return {"id": document_id, "application_id": application_id, "version": 1}


@router.get("/documents/{document_id}")
def document(document_id: int, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
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
    return {**dict(row), "versions": [dict(v) for v in versions],
            "comments": [dict(c) for c in comments],
            "activity": [dict(a) for a in activity]}


@router.post("/documents/{document_id}/versions")
async def upload_version(
    document_id: int,
    file: UploadFile = File(...),
    user=Depends(__import__("files.main", fromlist=["current_user"]).current_user),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(415, "File type is not allowed")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is too large")
    with db.db() as con:
        doc = _document_access(con, user, document_id, write=True)
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
             file.content_type or mimetypes.guess_type(file.filename or "")[0],
             len(content), user["id"], now),
        )
        con.execute(
            "UPDATE documents SET current_version=?,status='Uploaded',updated_at=? WHERE id=?",
            (version, now, document_id),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "version_uploaded",
                  {"version": version, "name": Path(file.filename or "upload").name})
    return {"ok": True, "document_id": document_id, "version": version}


@router.get("/documents/{document_id}/versions/{version}/download")
def download_version(document_id: int, version: int, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
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
    return FileResponse(path, media_type=row["content_type"], filename=row["original_name"])


@router.post("/documents/{document_id}/comments")
def add_comment(document_id: int, body: str = Form(...), parent_id: int | None = Form(None),
                user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
    text = body.strip()
    if not text or len(text) > 10000:
        raise HTTPException(400, "Comment must contain 1-10000 characters")
    with db.db() as con:
        doc = _document_access(con, user, document_id, write=True)
        con.execute(
            "INSERT INTO comments(document_id,parent_id,author_user_id,body,created_at) VALUES(?,?,?,?,?)",
            (document_id, parent_id, user["id"], text, _now()),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "comment_added")
        _notify(con, doc["owner_user_id"], "comment_added", "A new comment was added to your document")
    return {"ok": True}


@router.post("/documents/{document_id}/comments/{comment_id}/resolve")
def resolve_comment(document_id: int, comment_id: int, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
    with db.db() as con:
        doc = _document_access(con, user, document_id, write=True)
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
def activity(application_id: str | None = None, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
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
def notifications(user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
    with db.db() as con:
        rows = con.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
    with db.db() as con:
        result = con.execute(
            "UPDATE notifications SET read_at=? WHERE id=? AND user_id=?",
            (_now(), notification_id, user["id"]),
        )
        if not result.rowcount:
            raise HTTPException(404, "Notification not found")
    return {"ok": True}


@router.get("/tasks")
def tasks(application_id: str | None = None, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
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
    user=Depends(__import__("files.main", fromlist=["current_user"]).current_user),
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
            _document_access(con, user, document_id)
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
def complete_task(task_id: int, user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
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
def share_document(document_id: int, user_id: int = Form(...), permission: str = Form("comment"),
                   user=Depends(__import__("files.main", fromlist=["current_user"]).current_user)):
    with db.db() as con:
        doc = _document_access(con, user, document_id, write=True)
        if user["role"] not in ("pi", "sps") and user["id"] != doc["owner_user_id"]:
            raise HTTPException(403, "You cannot share this document")
        target = con.execute("SELECT id FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        if not target:
            raise HTTPException(400, "Collaborator is not active")
        if permission not in ("comment", "upload"):
            raise HTTPException(400, "Permission must be comment or upload")
        con.execute(
            "INSERT INTO document_collaborators(document_id,user_id,permission) VALUES(?,?,?) "
            "ON CONFLICT(document_id,user_id) DO UPDATE SET permission=excluded.permission",
            (document_id, user_id, permission),
        )
        _activity(con, user["id"], doc["application_id"], document_id, "contributor_invited",
                  {"user_id": user_id, "permission": permission})
        _notify(con, user_id, "contributor_invited", "A document was shared with you")
    return {"ok": True}


@router.websocket("/ws/{application_id}")
async def collaboration_socket(websocket: WebSocket, application_id: str):
    token = websocket.cookies.get("session")
    user = db.user_for_session(token) if token else None
    if not user:
        await websocket.close(code=4401)
        return
    try:
        with db.db() as con:
            _user_can_access(con, user, application_id)
    except HTTPException:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.discard(websocket)
