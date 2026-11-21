"""Collaborators, guests, comments, tasks and the application chat (HTTP and WebSocket)."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import main
from tests.conftest import PDF_BYTES, invite_guest, sign_in, upload_document, verify_guest


def share(client, doc_id, user_id, can_comment=False, can_edit=False, due_at=""):
    return client.post(f"/api/collaboration/documents/{doc_id}/collaborators", data={
        "user_id": user_id, "can_comment": str(can_comment).lower(),
        "can_edit": str(can_edit).lower(), "due_at": due_at})


def user_id(client):
    return client.get("/api/me").json()["id"]


# ---------- collaborator permissions (signed-in university users) ----------
def test_edit_collaborator_can_upload_a_version(pi1, pi2):
    doc_id = upload_document(pi1).json()["id"]
    assert share(pi1, doc_id, user_id(pi2), can_comment=True, can_edit=True).status_code == 200
    r = pi2.post(f"/api/collaboration/documents/{doc_id}/versions", files={"file": ("v2.pdf", PDF_BYTES, "application/pdf")})
    assert r.status_code == 200, r.text


def test_comment_only_collaborator_cannot_upload(pi1, pi2):
    doc_id = upload_document(pi1).json()["id"]
    share(pi1, doc_id, user_id(pi2), can_comment=True)
    assert pi2.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "Looks good"}).status_code == 200
    r = pi2.post(f"/api/collaboration/documents/{doc_id}/versions", files={"file": ("v2.pdf", PDF_BYTES, "application/pdf")})
    assert r.status_code == 403


def test_view_only_collaborator_cannot_comment(pi1, pi2):
    doc_id = upload_document(pi1).json()["id"]
    share(pi1, doc_id, user_id(pi2))
    assert pi2.get(f"/api/collaboration/documents/{doc_id}").status_code == 200
    assert pi2.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "x"}).status_code == 403


def test_collaborator_cannot_reshare_owners_document(pi1, pi2, sps):
    doc_id = upload_document(pi1).json()["id"]
    share(pi1, doc_id, user_id(pi2), can_comment=True, can_edit=True)
    assert share(pi2, doc_id, user_id(sps)).status_code == 403


# ---------- comments ----------
def test_only_the_owning_pi_resolves_comments(pi1, pi2, sps):
    doc_id = upload_document(pi1).json()["id"]
    share(pi1, doc_id, user_id(pi2), can_comment=True)
    sps.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "Fix budget", "scope": "Budget Justification"})
    cid = pi1.get(f"/api/collaboration/documents/{doc_id}").json()["comments"][0]["id"]
    assert sps.post(f"/api/collaboration/documents/{doc_id}/comments/{cid}/resolve").status_code == 403
    assert pi2.post(f"/api/collaboration/documents/{doc_id}/comments/{cid}/resolve").status_code == 403
    assert pi1.post(f"/api/collaboration/documents/{doc_id}/comments/{cid}/resolve").status_code == 200
    assert pi1.post(f"/api/collaboration/documents/{doc_id}/comments/{cid}/resolve").status_code == 404
    owner_notes = pi1.get("/api/collaboration/notifications").json()
    assert any(n["event_type"] == "comment_added" for n in owner_notes)


def test_empty_comment_rejected(pi1):
    doc_id = upload_document(pi1).json()["id"]
    assert pi1.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "   "}).status_code == 400


# ---------- guests ----------
def test_guest_is_limited_to_invited_application(pi1):
    guest = verify_guest(invite_guest(pi1))
    me = guest.get("/api/me").json()
    assert me["role"] == "guest" and me["application_id"] == "SAMPLE-001"
    assert [a["id"] for a in guest.get("/api/collaboration/applications").json()] == ["SAMPLE-001"]
    assert guest.get("/api/collaboration/documents?application_id=SAMPLE-003").status_code == 403
    assert guest.get("/api/collaboration/applications/SAMPLE-003/chat").status_code == 403
    assert upload_document(guest).status_code == 403
    assert guest.post("/api/collaboration/invitations", data={"application_id": "SAMPLE-001", "email": "x@y.z"}).status_code == 403


def test_guest_without_chat_permission_cannot_post_chat(pi1):
    guest = verify_guest(invite_guest(pi1, permissions="view,comment"))
    r = guest.post("/api/collaboration/applications/SAMPLE-001/chat", data={"body": "hello"})
    assert r.status_code == 403


def test_guest_with_chat_permission_can_post_chat(pi1):
    guest = verify_guest(invite_guest(pi1))
    assert guest.post("/api/collaboration/applications/SAMPLE-001/chat", data={"body": "hello"}).status_code == 200
    assert pi1.get("/api/collaboration/applications/SAMPLE-001/chat").json()[-1]["body"] == "hello"


def test_guest_needs_assignment_to_comment(pi1):
    doc_id = upload_document(pi1).json()["id"]
    guest = verify_guest(invite_guest(pi1))
    assert guest.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "hi"}).status_code == 403
    assert share(pi1, doc_id, user_id(guest), can_comment=True).status_code == 200
    assert guest.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "hi"}).status_code == 200


def test_guest_reverifying_keeps_same_account_and_assignments(pi1):
    doc_id = upload_document(pi1).json()["id"]
    token = invite_guest(pi1)
    first = verify_guest(token)
    share(pi1, doc_id, user_id(first), can_comment=True)
    second = verify_guest(token)   # e.g. a new browser, or after the session expired
    assert user_id(second) == user_id(first)
    assert second.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "still here"}).status_code == 200


def test_verified_guest_stays_assignable_after_signing_out(pi1):
    guest = verify_guest(invite_guest(pi1))
    gid = user_id(guest)
    guest.post("/auth/logout")
    ids = [g["id"] for g in pi1.get("/api/collaboration/application-guests?application_id=SAMPLE-001").json()]
    assert ids == [gid]


def test_guest_code_is_cancelled_after_repeated_wrong_guesses(pi1):
    token = invite_guest(pi1)
    c = TestClient(main.app)
    code = c.post("/api/collaboration/guest/request-code", data={"token": token}).json()["development_code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert c.post("/api/collaboration/guest/verify", data={"token": token, "code": wrong}).status_code == 401
    r = c.post("/api/collaboration/guest/verify", data={"token": token, "code": code})
    assert r.status_code == 400 and "new verification code" in r.json()["detail"]


def test_bad_invitation_token(app_started):
    c = TestClient(main.app)
    assert c.post("/api/collaboration/guest/request-code", data={"token": "nope"}).status_code == 400


# ---------- tasks ----------
def test_assignment_with_deadline_creates_task(pi1, pi2):
    doc_id = upload_document(pi1).json()["id"]
    share(pi1, doc_id, user_id(pi2), can_comment=True, due_at="2026-12-01T17:00")
    tasks = pi2.get("/api/collaboration/tasks").json()
    assert len(tasks) == 1 and tasks[0]["due_at"] == "2026-12-01T17:00" and tasks[0]["status"] == "Pending"
    assert pi1.post(f"/api/collaboration/tasks/{tasks[0]['id']}/complete").status_code == 403
    assert pi2.post(f"/api/collaboration/tasks/{tasks[0]['id']}/complete").status_code == 200


def test_assignment_without_deadline_creates_no_task(pi1, pi2):
    doc_id = upload_document(pi1).json()["id"]
    share(pi1, doc_id, user_id(pi2), can_comment=True, due_at="")
    assert pi2.get("/api/collaboration/tasks").json() == []


# ---------- chat over HTTP and WebSocket ----------
def test_chat_attachment_round_trip(pi1, sps):
    r = pi1.post("/api/collaboration/applications/SAMPLE-001/chat", data={"body": ""},
                 files={"file": ("budget.pdf", PDF_BYTES, "application/pdf")})
    assert r.status_code == 200, r.text
    url = r.json()["attachment"]["url"]
    assert sps.get(url).content == PDF_BYTES


def test_websocket_broadcasts_and_saves(pi1, sps):
    with pi1.websocket_connect("/api/collaboration/ws/SAMPLE-001") as a, \
         sps.websocket_connect("/api/collaboration/ws/SAMPLE-001") as b:
        a.send_json({"type": "message", "body": "Budget is ready"})
        assert a.receive_json()["body"] == "Budget is ready"
        assert b.receive_json()["author_role"] == "pi"
    assert sps.get("/api/collaboration/applications/SAMPLE-001/chat").json()[-1]["body"] == "Budget is ready"


def test_websocket_refuses_other_pis_application(pi2):
    with pytest.raises(WebSocketDisconnect) as exc:
        with pi2.websocket_connect("/api/collaboration/ws/SAMPLE-001") as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_websocket_stops_working_after_sign_out(pi1):
    from backend import collaboration
    with pi1.websocket_connect("/api/collaboration/ws/SAMPLE-001") as ws:
        pi1.post("/auth/logout")
        ws.send_json({"type": "message", "body": "sent after sign-out"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
    assert not collaboration._connections
    sps = sign_in("sps.demo1", "staff")
    bodies = [m["body"] for m in sps.get("/api/collaboration/applications/SAMPLE-001/chat").json()]
    assert "sent after sign-out" not in bodies


def test_websocket_bad_message_does_not_leak_connection(pi1):
    from backend import collaboration
    with pi1.websocket_connect("/api/collaboration/ws/SAMPLE-001") as ws:
        ws.send_text("not json")
        ws.send_json(["not", "an", "object"])
        ws.send_json({"type": "message", "body": "still alive"})
        assert ws.receive_json()["body"] == "still alive"
    assert not collaboration._connections
