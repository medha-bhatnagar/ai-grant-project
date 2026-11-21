"""PDF and document upload, storage, versioning, preview and download."""
from backend import config
from tests.conftest import PDF_BYTES, upload_document


def test_upload_then_download_returns_identical_bytes(pi1):
    r = upload_document(pi1)
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    d = pi1.get(f"/api/collaboration/documents/{doc_id}").json()
    assert d["current_version"] == 1 and d["versions"][0]["original_name"] == "aims.pdf"
    dl = pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/download")
    assert dl.status_code == 200 and dl.content == PDF_BYTES
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["content-disposition"].startswith("attachment")


def test_pdf_preview_is_inline(pi1):
    doc_id = upload_document(pi1).json()["id"]
    r = pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/preview")
    assert r.status_code == 200 and r.content == PDF_BYTES
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_pdf_preview_works_when_browser_sends_generic_type(pi1):
    """Browsers and drag-and-drop sometimes send application/octet-stream for PDFs.
    The page shows 'Open PDF' based on the file name, so the server must agree."""
    doc_id = upload_document(pi1, content_type="application/octet-stream").json()["id"]
    assert pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/preview").status_code == 200
    dl = pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/download")
    assert dl.headers["content-type"] == "application/pdf"


def test_download_type_is_not_taken_from_the_uploader(pi1):
    """A client-supplied text/html type must not be echoed back when the file is downloaded."""
    doc_id = upload_document(pi1, filename="notes.txt", content=b"<script>alert(1)</script>",
                             content_type="text/html").json()["id"]
    dl = pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/download")
    assert dl.headers["content-type"].startswith("text/plain")
    assert pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/preview").status_code == 415


def test_file_named_pdf_that_is_not_a_pdf_is_rejected(pi1):
    r = upload_document(pi1, filename="fake.pdf", content=b"MZ not a pdf")
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_disallowed_extension_rejected(pi1):
    r = upload_document(pi1, filename="run.exe", content=b"MZ", content_type="application/octet-stream")
    assert r.status_code == 415


def test_oversized_upload_rejected(pi1, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    assert upload_document(pi1).status_code == 413


def test_new_version_keeps_old_version(pi1):
    doc_id = upload_document(pi1).json()["id"]
    v2 = PDF_BYTES + b"% second\n"
    r = pi1.post(f"/api/collaboration/documents/{doc_id}/versions", files={"file": ("aims-v2.pdf", v2, "application/pdf")})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/download").content == PDF_BYTES
    assert pi1.get(f"/api/collaboration/documents/{doc_id}/versions/2/download").content == v2
    assert pi1.get(f"/api/collaboration/documents/{doc_id}/versions/9/download").status_code == 404


def test_missing_stored_file_is_a_clear_404(pi1, fresh_storage):
    doc_id = upload_document(pi1).json()["id"]
    for f in (fresh_storage / "uploads").iterdir():
        f.unlink()
    r = pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/download")
    assert r.status_code == 404 and r.json()["detail"] == "Stored file is missing"


def test_sps_can_view_any_document(pi1, sps):
    doc_id = upload_document(pi1).json()["id"]
    assert sps.get(f"/api/collaboration/documents/{doc_id}/versions/1/preview").status_code == 200


def test_document_list_counts_match(pi1):
    upload_document(pi1)
    apps = {a["id"]: a for a in pi1.get("/api/collaboration/applications").json()}
    docs = pi1.get("/api/collaboration/documents?application_id=SAMPLE-001").json()
    assert apps["SAMPLE-001"]["document_count"] == len(docs) == 2  # seeded sample + upload


def test_pi_can_open_document_that_sps_uploaded_to_their_application(pi1, sps):
    """Documents have their own uploader; access must follow the APPLICATION owner."""
    doc_id = upload_document(sps, name="SPS checklist").json()["id"]
    r = pi1.get(f"/api/collaboration/documents/{doc_id}")
    assert r.status_code == 200, r.text
    assert r.json()["owner_name"] == "Demo SPS Staff"   # who uploaded it, shown in the page
    assert pi1.get(f"/api/collaboration/documents/{doc_id}/versions/1/download").status_code == 200
    assert pi1.post(f"/api/collaboration/documents/{doc_id}/comments", data={"body": "Thanks"}).status_code == 200
