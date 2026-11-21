"""Settings. They come from environment variables, so nothing secret is written in the code."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # the backend/ folder


def secret(name, default=""):
    """Read a secret from a Docker secret file (/run/secrets/<name>), a NAME_FILE path, or the NAME variable."""
    path = Path(os.environ.get(f"{name}_FILE", f"/run/secrets/{name.lower()}"))
    return path.read_text().strip() if path.is_file() else os.environ.get(name, default)


AUTH_PROVIDER = os.environ.get("AUTH_PROVIDER", "demo")  # demo | cas | oidc
ALLOW_DEMO = os.environ.get("ALLOW_DEMO", "1") == "1"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
DB_PATH = os.environ.get("DB_PATH", str(BASE / "data" / "app.db"))
USERS_CSV = os.environ.get(
    "ALLOWED_USERS_CSV",
    os.environ.get("USERS_CSV", str(BASE / "allowed_users.demo.csv")),
)
FRONTEND_DIR = os.environ.get("FRONTEND_DIR", str(BASE.parent / "frontend"))
STORAGE_DIR = os.environ.get("STORAGE_DIR", str(BASE / "data" / "uploads"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
ALLOWED_UPLOAD_EXTENSIONS = {
    item.strip().lower()
    for item in os.environ.get(
        "ALLOWED_UPLOAD_EXTENSIONS", ".pdf,.doc,.docx,.xls,.xlsx,.txt"
    ).split(",")
    if item.strip()
}
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "8"))
SESSION_SECRET = secret("SESSION_SECRET", "dev-only-change-me")
COOKIE_SECURE = PUBLIC_URL.startswith("https://")
GUEST_INVITATION_HOURS = int(os.environ.get("GUEST_INVITATION_HOURS", "72"))
GUEST_CODE_MINUTES = int(os.environ.get("GUEST_CODE_MINUTES", "10"))

CAS_SERVER_URL = os.environ.get("CAS_SERVER_URL", "").rstrip("/")  # from UConn IT
OIDC_METADATA_URL = os.environ.get("OIDC_METADATA_URL", "")  # from UConn IT (Entra)
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = secret("OIDC_CLIENT_SECRET")
OIDC_USER_CLAIM = os.environ.get("OIDC_USER_CLAIM", "preferred_username")
GRAPH_TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = secret("GRAPH_CLIENT_SECRET")
GRAPH_SENDER_EMAIL = os.environ.get("GRAPH_SENDER_EMAIL", "")
