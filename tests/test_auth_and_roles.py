"""Sign-in, sign-out and role-based access. The backend must enforce all of these, not the page."""
from fastapi.testclient import TestClient

from backend import main
from tests.conftest import sign_in, upload_document


def test_api_requires_sign_in(app_started):
    anon = TestClient(main.app)
    for path in ("/api/me", "/api/collaboration/applications", "/api/collaboration/documents",
                 "/api/collaboration/tasks", "/api/requests"):
        assert anon.get(path).status_code == 401, path


def test_health_is_public(app_started):
    assert TestClient(main.app).get("/api/health").json() == {"ok": True}


def test_me_reports_role(pi1, sps):
    assert pi1.get("/api/me").json()["role"] == "pi"
    assert sps.get("/api/me").json()["role"] == "sps"


def test_pi_using_staff_entrance_is_refused(app_started):
    c = TestClient(main.app)
    c.get("/auth/login?as=staff", follow_redirects=False)
    r = c.get("/auth/demo/login?login_id=pi.demo1", follow_redirects=False)
    assert r.status_code == 303 and "wrong_entrance" in r.headers["location"]
    assert c.get("/api/me").status_code == 401


def test_unknown_user_cannot_sign_in(app_started):
    c = TestClient(main.app)
    assert c.get("/auth/demo/login?login_id=nobody", follow_redirects=False).status_code == 404


def test_sign_out_ends_session(pi1):
    assert pi1.post("/auth/logout").status_code == 200
    assert pi1.get("/api/me").status_code == 401


def test_pi_sees_only_own_applications(pi1, pi2, sps):
    assert {a["id"] for a in pi1.get("/api/collaboration/applications").json()} == {"SAMPLE-001", "SAMPLE-003"}
    assert {a["id"] for a in pi2.get("/api/collaboration/applications").json()} == {"SAMPLE-002"}
    assert {a["id"] for a in sps.get("/api/collaboration/applications").json()} == {"SAMPLE-001", "SAMPLE-002", "SAMPLE-003"}
    # The older /api/requests endpoint uses the same scoping
    assert {a["id"] for a in pi2.get("/api/requests").json()} == {"SAMPLE-002"}


def test_pi_cannot_reach_another_pis_application(pi1, pi2):
    doc_id = upload_document(pi1).json()["id"]
    assert pi2.get(f"/api/collaboration/documents/{doc_id}").status_code == 403
    assert pi2.get(f"/api/collaboration/documents/{doc_id}/versions/1/download").status_code == 403
    assert pi2.get("/api/collaboration/documents?application_id=SAMPLE-001").status_code == 403
    assert pi2.get("/api/collaboration/applications/SAMPLE-001/chat").status_code == 403
    assert upload_document(pi2, "SAMPLE-001").status_code == 403


def test_only_sps_changes_application_status(pi1, sps):
    assert pi1.post("/api/collaboration/applications/SAMPLE-001/status", data={"status": "Approved"}).status_code == 403
    r = sps.post("/api/collaboration/applications/SAMPLE-001/status", data={"status": "Approved"})
    assert r.status_code == 200
    assert {a["id"]: a["status"] for a in pi1.get("/api/collaboration/applications").json()}["SAMPLE-001"] == "Approved"
    assert sps.post("/api/collaboration/applications/SAMPLE-001/status", data={"status": "Bogus"}).status_code == 400
    assert sps.post("/api/collaboration/applications/NOPE/status", data={"status": "Approved"}).status_code == 404


def test_staff_overview_is_sps_only(pi1, sps):
    assert pi1.get("/api/staff/overview").status_code == 403
    assert sps.get("/api/staff/overview").json()["total"] == 3


def test_sign_in_page_lists_only_matching_accounts(app_started):
    c = TestClient(main.app)
    c.get("/auth/login?as=staff", follow_redirects=False)
    page = c.get("/auth/demo").text
    assert "sps.demo1" in page and "pi.demo1" not in page


def test_backend_source_and_user_list_are_not_downloadable(app_started):
    """The frontend folder also holds the Python code, the user list and backups. Only page assets may be served."""
    anon = TestClient(main.app)
    for path in ("/main.py", "/config.py", "/db.py", "/auth.py", "/__init__.py", "/requirements.txt",
                 "/allowed_users.demo.csv", "/index.html.bak", "/main.py.before-ui-redesign.bak",
                 "/fonts/OFL-Atkinson.txt", "/fonts/"):
        assert anon.get(path).status_code == 404, path
    for path in ("/", "/index.html", "/app.css", "/fonts/atkinson-hyperlegible-next-latin-400-normal.woff2"):
        assert anon.get(path).status_code == 200, path
