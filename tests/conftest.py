"""Shared test setup.

Every test gets its own empty SQLite database and upload folder, so tests never
touch real data and never depend on each other. Users come from the fictional
demo CSV (pi.demo1, pi.demo2, sps.demo1, laura.demo).
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Settings are read at import time, so set safe test values before importing the app.
os.environ.setdefault("AUTH_PROVIDER", "demo")
os.environ.setdefault("ALLOW_DEMO", "1")
os.environ["USERS_CSV"] = str(ROOT / "backend" / "allowed_users.demo.csv")
os.environ["FRONTEND_DIR"] = str(ROOT / "dashboard")

from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend import main  # noqa: E402

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def fresh_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "uploads"))
    from backend import collaboration
    collaboration._connections.clear()
    getattr(collaboration, "_code_failures", {}).clear()
    yield tmp_path


@pytest.fixture
def app_started(fresh_storage):
    """A client whose context manager runs startup (creates tables, seeds demo users and samples)."""
    with TestClient(main.app) as c:
        yield c


def sign_in(login_id, entrance="pi"):
    """Sign in through the real demo flow and return a client holding the session cookie."""
    c = TestClient(main.app)
    c.get(f"/auth/login?as={entrance}", follow_redirects=False)
    r = c.get(f"/auth/demo/login?login_id={login_id}", follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


@pytest.fixture
def pi1(app_started):
    return sign_in("pi.demo1")


@pytest.fixture
def pi2(app_started):
    return sign_in("pi.demo2")


@pytest.fixture
def sps(app_started):
    return sign_in("sps.demo1", "staff")


def upload_document(client, application_id="SAMPLE-001", name="Specific Aims",
                    filename="aims.pdf", content=PDF_BYTES, content_type="application/pdf"):
    return client.post(
        "/api/collaboration/documents",
        data={"application_id": application_id, "name": name, "category": "Research plan"},
        files={"file": (filename, content, content_type)},
    )


def invite_guest(inviter, application_id="SAMPLE-001", email="collab@example.org",
                 permissions="view,chat,comment"):
    r = inviter.post("/api/collaboration/invitations", data={
        "application_id": application_id, "email": email, "permissions": permissions})
    assert r.status_code == 200, r.text
    return re.search(r"token=([^&]+)", r.json()["invitation_url"]).group(1)


def verify_guest(token, name="Outside Collaborator"):
    """Run the real guest verification flow (demo mode returns the code in the response)."""
    c = TestClient(main.app)
    code = c.post("/api/collaboration/guest/request-code", data={"token": token}).json()["development_code"]
    r = c.post("/api/collaboration/guest/verify", data={"token": token, "code": code, "display_name": name})
    assert r.status_code == 200, r.text
    return c
