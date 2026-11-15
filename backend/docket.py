"""The collaboration docket in the portal (the professor's Monday demo). Signed-in users only.

GET  /api/docket                        every docket: investigators, documents, CV and training status, outbox
POST /api/docket/{docket_id}/upload     an investigator's document (drag and drop): investigator, doc_type, file
GET  /api/docket/files/{doc_id}         download an uploaded document
POST /api/docket/reset                  empty the demo (SPS staff only)

The data itself lives in judge/docket.py (one JSON file plus the uploaded files, no database).
"""
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))
import docket as store  # noqa: E402

_main = __import__(__package__ + ".main", fromlist=["current_user", "require_role"])
router = APIRouter(prefix="/api/docket", tags=["docket"])


@router.get("")
def everything(user=Depends(_main.current_user)):
    return store.everything()


@router.post("/{docket_id}/upload")
async def upload(docket_id: str, investigator: str = Form(...), doc_type: str = Form(...),
                 file: UploadFile = File(...), user=Depends(_main.current_user)):
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        doc_id = store.add_document(docket_id, investigator, doc_type, file.filename or "upload.pdf", content,
                                    uploaded_by=user["display_name"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": doc_id}


@router.get("/files/{doc_id}")
def download(doc_id: str, user=Depends(_main.current_user)):
    path, name = store.document_file(doc_id)
    if not path or not path.is_file():
        raise HTTPException(404, "No such document")
    return FileResponse(path, filename=name)


@router.post("/reset")
def reset(user=Depends(_main.require_role("sps"))):
    store.reset()
    return {"ok": True}
