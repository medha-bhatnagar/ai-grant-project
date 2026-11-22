"""End-to-end checks of the real pages in Chrome. Each test names the behavior a user relies on."""
import json
import os
from pathlib import Path

import pytest

from tests.e2e.conftest import PDF_BYTES, api_client, seed_pdf

pytestmark = pytest.mark.e2e


def settle(w, ms=600):
    w.page.wait_for_load_state("networkidle")
    w.page.wait_for_timeout(ms)


def open_document(w, name, application_id=None):
    if application_id and w.page.locator("#live-app").count():
        w.page.select_option("#live-app", application_id)
        w.page.locator("#live-docs [data-live-id]").first.wait_for()
    w.page.locator("[data-live-id]", has_text=name).click()
    w.page.locator("#live-detail h3", has_text=name).wait_for()


# ---------- page loads ----------
def test_investigator_page_loads_cleanly_with_one_chat_connection(open_as):
    w = open_as("pi.demo1")
    settle(w)
    assert w.page.locator("#view-pi").is_visible()
    assert w.errors == []
    assert len(w.open_sockets()) == 1, [ws.url for ws in w.open_sockets()]


def test_staff_page_opens_no_hidden_investigator_connections(open_as):
    w = open_as("sps.demo1", "staff")
    settle(w)
    assert w.page.locator("#view-staff").is_visible()
    assert w.page.locator("#s-list [data-app-id]").count() == 3
    assert w.open_sockets() == []          # nothing until Collaboration is opened
    tabs = w.page.locator("#s-inner")
    for _ in range(3):                     # moving between tabs must not pile up connections
        tabs.get_by_role("tab", name="Collaboration").click()
        settle(w, 300)
        tabs.get_by_role("tab", name="Overview").click()
        settle(w, 300)
    tabs.get_by_role("tab", name="Collaboration").click()
    settle(w)
    assert len(w.open_sockets()) == 1
    assert w.errors == []


def test_signed_out_visitor_sees_sign_in_without_errors(browser, server):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/#staff")
    page.wait_for_load_state("networkidle")
    assert page.locator("#view-login").is_visible() and errors == []
    page.close()


def test_sign_out_closes_live_chat_and_clears_page(open_as):
    w = open_as("pi.demo1")
    settle(w)
    w.page.locator("#view-pi [data-signout]").click()
    w.page.locator("#view-login").wait_for()
    settle(w)
    assert w.open_sockets() == []
    assert w.page.locator("[data-live-id]").count() == 0     # no documents left in the page
    assert w.page.locator("#p-col").inner_text().strip() == ""


# ---------- PDF review ----------
def test_pdf_review_accepts_several_comments_without_reloading(open_as, server):
    seed_pdf(server)
    w = open_as("pi.demo1")
    settle(w)
    open_document(w, "Specific Aims", "SAMPLE-001")
    w.page.get_by_role("button", name="Open PDF").click()
    modal = w.page.locator("#pdf-review-modal")
    assert modal.is_visible()
    for text in ("First note", "Second note"):
        modal.get_by_placeholder("Add a comment").fill(text)
        modal.get_by_role("button", name="Comment").click()
        modal.get_by_text(text).wait_for()
    assert modal.is_visible(), "the dialog closed: the form was submitted natively and the page reloaded"
    assert "body=" not in w.page.url
    assert w.errors == []


def test_resolving_in_pdf_review_updates_the_list(open_as, server):
    doc_id = seed_pdf(server)
    api_client(server, "sps.demo1", "staff").post(
        f"/api/collaboration/documents/{doc_id}/comments", data={"body": "Please fix the aims"})
    w = open_as("pi.demo1")
    settle(w)
    open_document(w, "Specific Aims", "SAMPLE-001")
    w.page.get_by_role("button", name="Open PDF").click()
    modal = w.page.locator("#pdf-review-modal")
    modal.get_by_role("button", name="Resolve").click()
    modal.get_by_text("Resolved").wait_for()
    assert modal.get_by_role("button", name="Resolve").count() == 0
    assert w.errors == []


def test_pdf_frame_shows_the_document(open_as, server):
    seed_pdf(server)
    w = open_as("pi.demo1")
    settle(w)
    open_document(w, "Specific Aims", "SAMPLE-001")
    with w.page.expect_response(lambda r: "/preview" in r.url) as resp:
        w.page.get_by_role("button", name="Open PDF").click()
    assert resp.value.status == 200 and resp.value.headers["content-type"] == "application/pdf"


# ---------- uploads ----------
def test_new_document_form_offers_only_my_real_applications(open_as, tmp_path):
    w = open_as("pi.demo2")
    settle(w)
    w.page.locator("#p-tabs").get_by_role("tab", name="Ready to submit").click()
    options = w.page.locator("#new-doc-app option").evaluate_all("o => o.map(x => x.value)")
    assert options == ["SAMPLE-002"]
    f = tmp_path / "budget.pdf"
    f.write_bytes(PDF_BYTES)
    w.page.fill("#new-doc-name", "Budget")
    w.page.set_input_files("#new-doc-file", str(f))
    w.page.locator("#new-doc-status", has_text="Uploaded").wait_for()
    w.page.locator("#p-tabs").get_by_role("tab", name="Collaboration").click()
    w.page.locator("[data-live-id]", has_text="Budget").wait_for()
    assert w.errors == []


def test_upload_errors_are_shown_in_plain_language(open_as, tmp_path):
    w = open_as("pi.demo1")
    settle(w)
    w.page.locator("#p-tabs").get_by_role("tab", name="Ready to submit").click()
    f = tmp_path / "tool.exe"
    f.write_bytes(b"MZ")
    w.page.set_input_files("#new-doc-file", str(f))
    status = w.page.locator("#new-doc-status")
    status.filter(has_text="not allowed").wait_for()
    assert "{" not in status.inner_text() and "detail" not in status.inner_text()


def test_new_version_upload_from_document_page(open_as, server, tmp_path):
    seed_pdf(server)
    w = open_as("pi.demo1")
    settle(w)
    open_document(w, "Specific Aims", "SAMPLE-001")
    f = tmp_path / "aims-v2.pdf"
    f.write_bytes(PDF_BYTES + b"% v2\n")
    w.page.set_input_files("#live-upload", str(f))
    w.page.locator("#live-detail", has_text="Version 2: aims-v2.pdf").wait_for()
    assert w.errors == []


# ---------- deadlines ----------
def test_my_deadlines_stay_visible_when_moving_the_calendar(open_as, server):
    doc_id = seed_pdf(server)
    pi1 = api_client(server, "pi.demo1")
    pi2_id = api_client(server, "pi.demo2").get("/api/me").json()["id"]
    pi1.post(f"/api/collaboration/documents/{doc_id}/collaborators",
             data={"user_id": pi2_id, "can_comment": "true", "due_at": "2026-12-01T17:00"})
    w = open_as("pi.demo2")
    settle(w)
    w.page.locator("#p-tabs").get_by_role("tab", name="Deadlines").click()
    w.page.get_by_text("contribution: Specific Aims").wait_for()
    w.page.get_by_role("button", name="Next").click()
    w.page.get_by_role("button", name="Month").click()
    assert w.page.get_by_text("contribution: Specific Aims").count() == 1
    assert w.page.get_by_role("heading", name="My contribution deadlines").count() == 1


# ---------- live chat ----------
def test_chat_message_reaches_the_other_person_live(open_as):
    pi = open_as("pi.demo1")
    sps = open_as("sps.demo1", "staff")
    settle(pi)
    pi.page.select_option("#live-app", "SAMPLE-003")
    settle(pi)
    sps.page.locator("#s-list [data-app-id='SAMPLE-003']").click()
    sps.page.locator("#s-inner").get_by_role("tab", name="Collaboration").click()
    settle(sps)
    sps.page.locator("#s-panel summary", has_text="General application chat").click()
    pi.page.locator("#p-col summary", has_text="General application chat").click()
    pi.page.get_by_placeholder("Message the application team").fill("Budget draft is ready")
    pi.page.locator("#app-chat-form").get_by_role("button", name="Send").click()
    sps.page.locator("#app-chat-log", has_text="Budget draft is ready").wait_for(timeout=5000)
    assert pi.page.locator("#app-chat-log").get_by_text("Budget draft is ready").count() == 1
    assert pi.errors == [] and sps.errors == []


# ---------- SPS review actions ----------
def test_sps_approve_persists(open_as, server):
    w = open_as("sps.demo1", "staff")
    settle(w)
    w.page.locator("#s-list [data-app-id='SAMPLE-002']").click()
    w.page.get_by_role("button", name="Approve").click()
    w.page.locator("#s-status", has_text="Approved").wait_for()
    apps = api_client(server, "pi.demo2").get("/api/collaboration/applications").json()
    assert apps[0]["status"] == "Approved"


# ---------- accessibility ----------
AXE = os.environ.get("AXE_JS") or str(Path(__file__).resolve().parents[2] / "node_modules/axe-core/axe.min.js")


@pytest.mark.parametrize("login_id,entrance,view", [(None, None, "#view-login"),
                                                     ("pi.demo1", "pi", "#view-pi"),
                                                     ("sps.demo1", "staff", "#view-staff")])
def test_no_serious_accessibility_problems(open_as, browser, server, login_id, entrance, view):
    if not Path(AXE).is_file():
        pytest.skip("axe-core not found: npm install --no-save axe-core, or set AXE_JS")
    if login_id:
        page = open_as(login_id, entrance).page
    else:
        page = browser.new_page()
        page.goto(server + "/")
    page.wait_for_load_state("networkidle")
    page.add_script_tag(path=AXE)
    result = page.evaluate(f"axe.run(document.querySelector('{view}'))")
    bad = [f"{v['id']} ({v['impact']}): {v['help']} -> {[n['target'] for n in v['nodes']][:3]}"
           for v in result["violations"] if v["impact"] in ("serious", "critical")]
    assert bad == [], json.dumps(bad, indent=1)


def test_investigator_workspace_is_scoped_to_one_named_application(open_as, server):
    seed_pdf(server, application_id="SAMPLE-001", name="Aims for 001")
    w = open_as("pi.demo1")
    settle(w)
    w.page.select_option("#live-app", "SAMPLE-003")
    settle(w)
    assert w.page.locator("[data-live-id]", has_text="Aims for 001").count() == 0
    assert "SAMPLE-003" in w.page.locator("#p-col h2 + span").inner_text()
    assert len(w.open_sockets()) == 1 and w.open_sockets()[0].url.endswith("/ws/SAMPLE-003")
    w.page.select_option("#live-app", "SAMPLE-001")
    w.page.locator("[data-live-id]", has_text="Aims for 001").wait_for()
    settle(w)
    assert len(w.open_sockets()) == 1 and w.open_sockets()[0].url.endswith("/ws/SAMPLE-001")
    assert w.errors == []


@pytest.mark.parametrize("permissions,chat_enabled", [("view,chat,comment", True), ("view,comment", False)])
def test_invited_guest_flow(browser, server, permissions, chat_enabled):
    import re
    seed_pdf(server)
    pi = api_client(server, "pi.demo1")
    r = pi.post("/api/collaboration/invitations",
                data={"application_id": "SAMPLE-001", "email": "guest@example.org", "permissions": permissions})
    link = r.json()["invitation_url"]
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.locator("#status code").wait_for()                       # demo mode shows the code on screen
    page.get_by_role("button", name="Verify and continue").click()
    page.wait_for_url(re.compile(r"#guest$"))
    page.get_by_role("button", name="Open request").click()
    page.locator("[data-live-id]", has_text="Specific Aims").wait_for()
    assert page.get_by_text("Invite external collaborator").count() == 0
    assert page.locator("#app-chat-form").count() == (1 if chat_enabled else 0)
    if not chat_enabled:
        assert page.get_by_text("Chat is not enabled for your invitation.").count() == 1
    assert page.locator("#live-dropzone").count() == 0           # no upload assignment yet
    assert errors == []
    ctx.close()


def test_chat_recovers_after_connection_drops(open_as, server):
    w = open_as("pi.demo1")
    settle(w)
    w.page.select_option("#live-app", "SAMPLE-001")
    settle(w)
    w.page.locator("#p-col summary", has_text="General application chat").click()
    w.page.evaluate("LIVE['view-pi'].socket.close()")          # simulate a network drop
    api_client(server, "sps.demo1", "staff").post(               # someone writes while it is down
        "/api/collaboration/applications/SAMPLE-001/chat", data={"body": "Sent during the outage"})
    w.page.locator("#app-chat-log", has_text="Sent during the outage").wait_for(timeout=8000)
    settle(w)
    assert len(w.open_sockets()) == 1
    assert w.errors == []
