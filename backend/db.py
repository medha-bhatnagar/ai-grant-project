"""The app's own database (SQLite): who may sign in, who is signed in now, and a log of sign-in attempts."""
import csv
import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(          -- the guest list: only these people may use the app
  id INTEGER PRIMARY KEY,
  login_id TEXT UNIQUE NOT NULL,           -- NetID, or whatever name the sign-in service returns
  email TEXT, display_name TEXT,
  role TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, last_login_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(       -- the wristbands: who is signed in right now
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  application_id TEXT REFERENCES applications(id),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_events(   -- a log of every sign-in attempt, good or bad
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, provider TEXT NOT NULL,
  login_id TEXT, success INTEGER NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS applications(
  id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Incomplete',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
  name TEXT NOT NULL, category TEXT NOT NULL, owner_user_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'Draft', current_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_versions(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
  version INTEGER NOT NULL, original_name TEXT NOT NULL, storage_key TEXT NOT NULL,
  content_type TEXT, size_bytes INTEGER NOT NULL, uploaded_by INTEGER NOT NULL REFERENCES users(id),
  uploaded_at TEXT NOT NULL, UNIQUE(document_id, version));
CREATE TABLE IF NOT EXISTS document_collaborators(
  document_id INTEGER NOT NULL REFERENCES documents(id), user_id INTEGER NOT NULL REFERENCES users(id),
  permission TEXT NOT NULL DEFAULT 'view',
  scope TEXT NOT NULL DEFAULT '',
  can_comment INTEGER NOT NULL DEFAULT 0,
  can_edit INTEGER NOT NULL DEFAULT 0,
  due_at TEXT,
  PRIMARY KEY(document_id, user_id));
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
  version_id INTEGER REFERENCES document_versions(id), parent_id INTEGER REFERENCES comments(id),
  author_user_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  resolved_at TEXT, resolved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
  document_id INTEGER REFERENCES documents(id), description TEXT NOT NULL,
  assigned_to INTEGER NOT NULL REFERENCES users(id), created_by INTEGER NOT NULL REFERENCES users(id),
  due_at TEXT, status TEXT NOT NULL DEFAULT 'Pending', priority TEXT NOT NULL DEFAULT 'Normal',
  created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS activity_logs(
  id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
  application_id TEXT REFERENCES applications(id), document_id INTEGER REFERENCES documents(id),
  event_type TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  event_type TEXT NOT NULL, message TEXT NOT NULL, read_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS guest_invitations(
  id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, application_id TEXT NOT NULL REFERENCES applications(id),
  invited_by INTEGER NOT NULL REFERENCES users(id), email TEXT NOT NULL, display_name TEXT,
  permissions TEXT NOT NULL DEFAULT '["view","chat","comment"]',
  expires_at TEXT NOT NULL, code_hash TEXT, code_expires_at TEXT, verified_at TEXT,
  revoked_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
  author_user_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL,
  attachment_name TEXT, attachment_storage_key TEXT, attachment_content_type TEXT,
  attachment_size_bytes INTEGER,
  edited_at TEXT, deleted_at TEXT, created_at TEXT NOT NULL);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()  # we store a hash, never the raw token


def init_db():
    with db() as con:
        _migrate_users_role_constraint(con)
        con.executescript(SCHEMA)
        collaborator_columns = {row["name"] for row in con.execute("PRAGMA table_info(document_collaborators)")}
        for column, definition in (
            ("scope", "TEXT NOT NULL DEFAULT ''"),
            ("can_comment", "INTEGER NOT NULL DEFAULT 0"),
            ("can_edit", "INTEGER NOT NULL DEFAULT 0"),
            ("due_at", "TEXT"),
        ):
            if column not in collaborator_columns:
                con.execute(f"ALTER TABLE document_collaborators ADD COLUMN {column} {definition}")
        comment_columns = {row["name"] for row in con.execute("PRAGMA table_info(comments)")}
        if "scope" not in comment_columns:
            con.execute("ALTER TABLE comments ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
        chat_columns = {row["name"] for row in con.execute("PRAGMA table_info(chat_messages)")}
        for column, definition in (
            ("attachment_name", "TEXT"),
            ("attachment_storage_key", "TEXT"),
            ("attachment_content_type", "TEXT"),
            ("attachment_size_bytes", "INTEGER"),
        ):
            if column not in chat_columns:
                con.execute(f"ALTER TABLE chat_messages ADD COLUMN {column} {definition}")
        columns = {row["name"] for row in con.execute("PRAGMA table_info(sessions)")}
        if "application_id" not in columns:
            con.execute("ALTER TABLE sessions ADD COLUMN application_id TEXT")
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
    seed_users()


def _migrate_users_role_constraint(con):
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not row or "'pi','sps'" not in (row["sql"] or ""):
        return
    con.execute("ALTER TABLE users RENAME TO users_legacy")
    con.execute(
        """CREATE TABLE users(
           id INTEGER PRIMARY KEY, login_id TEXT UNIQUE NOT NULL, email TEXT,
           display_name TEXT, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
           created_at TEXT NOT NULL, last_login_at TEXT)"""
    )
    con.execute(
        "INSERT INTO users SELECT id,login_id,email,display_name,role,active,created_at,last_login_at FROM users_legacy"
    )
    con.execute("DROP TABLE users_legacy")


def seed_users():
    """Load the guest list from the CSV file. New people are added, existing people are updated."""
    path = Path(config.USERS_CSV)
    if not path.is_file():
        return
    with db() as con, path.open(newline="") as f:
        for r in csv.DictReader(f):
            con.execute(
                """INSERT INTO users(login_id,email,display_name,role,active,created_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(login_id) DO UPDATE SET email=excluded.email,
                   display_name=excluded.display_name, role=excluded.role, active=excluded.active""",
                (r["login_id"].strip().lower(), r["email"].strip().lower(), r["display_name"].strip(),
                 r["role"].strip(), int(r.get("active") or 1), now()))


def find_user(*identifiers):
    """Look someone up by NetID/login name or email. Returns None if they are not on the guest list."""
    with db() as con:
        for ident in identifiers:
            if ident:
                row = con.execute("SELECT * FROM users WHERE active=1 AND (login_id=? OR email=?)",
                                  (ident.lower(), ident.lower())).fetchone()
                if row:
                    return dict(row)
    return None


def log_event(provider, login_id, success, reason=""):
    with db() as con:
        con.execute("INSERT INTO login_events(at,provider,login_id,success,reason) VALUES(?,?,?,?,?)",
                    (now(), provider, login_id, int(success), reason))


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)).isoformat(timespec="seconds")
    with db() as con:
        con.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                    (_hash(token), user_id, now(), expires))
        con.execute("UPDATE users SET last_login_at=? WHERE id=?", (now(), user_id))
    return token


def create_guest_session(user_id, application_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)).isoformat(timespec="seconds")
    with db() as con:
        con.execute(
            "INSERT INTO sessions(token_hash,user_id,application_id,created_at,expires_at) VALUES(?,?,?,?,?)",
            (_hash(token), user_id, application_id, now(), expires),
        )
    return token


def user_for_session(token):
    with db() as con:
        row = con.execute(
            "SELECT u.*, s.application_id AS session_application_id FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (_hash(token), now())).fetchone()
    return dict(row) if row else None


def end_session(token):
    with db() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))


def guest_permissions(user_id, application_id):
    with db() as con:
        row = con.execute(
            """SELECT permissions FROM guest_invitations
               WHERE invited_by IS NOT NULL AND email=(SELECT email FROM users WHERE id=?)
                 AND application_id=? AND verified_at IS NOT NULL AND revoked_at IS NULL
               ORDER BY verified_at DESC LIMIT 1""",
            (user_id, application_id),
        ).fetchone()
    if not row:
        return []
    import json
    return json.loads(row["permissions"])
