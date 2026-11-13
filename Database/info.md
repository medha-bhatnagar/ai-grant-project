# uploader.py

Turns documents into searchable chunks in MongoDB.

For each file it:
1. Reads it with docling (PDF, DOCX, PPTX, XLSX, HTML, MD).
2. Splits it into chunks of up to 256 tokens.
3. Embeds each chunk with `all-MiniLM-L6-v2` (384 numbers per chunk).
4. Saves the chunks to the collection you choose.

Re-uploading a document replaces its old chunks. Unchanged files are skipped.

```python
from pymongo import MongoClient
from uploader import DocumentUploader

db = MongoClient("mongodb://localhost:27017")["grant_rag"]
up = DocumentUploader(db)            # loads models once; reuse this object

up.upload_file("Announcements/Document1.pdf", "announcements")   # -> chunks stored
up.upload_folder("Past_Submissions", "past_submissions")         # -> {file: chunks or error}
up.delete_document("Announcements/Document1.pdf", "announcements")
```

Each chunk stores: `text`, `embedding`, `chunk_id`, `doc_link`, `source`,
`funding_source`, `doc_type`, `file_hash`, `embed_model`, `uploaded_at`.

## Docker notes

- **Base image:** `python:3.12-slim` works.
- **System packages:** `apt-get install -y libgl1 libglib2.0-0` (docling's PDF/OCR needs them).
- **Python packages:** install CPU-only PyTorch first to keep the image small
  (`pip install torch --index-url https://download.pytorch.org/whl/cpu`), then `pip install -r requirements.txt`.
- **Model cache:** the first run downloads ~1–2 GB of models. Set `HF_HOME` to a mounted volume so they aren't re-downloaded each time.
- **Memory:** give the container at least 4 GB RAM; large PDFs can need more.
- **MongoDB:** if MongoDB runs on the host, use `--network host` (Linux) or connect to `host.docker.internal`.
- **Files:** mount the document folders into the container so the code can read them.


# Relational Database
```
sudo mysql
use affiliation;
```