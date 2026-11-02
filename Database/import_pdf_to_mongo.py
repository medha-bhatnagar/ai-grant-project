"""Import ONE genuine NIAID PDF as searchable MongoDB document chunks.

Only writes to a deliberately isolated personal demo database by default.
Does not change Nina's upload.py and does NOT generate vector embeddings.
Usage:
  python import_pdf_to_mongo.py --dry-run
  python import_pdf_to_mongo.py
"""

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

PDF_NAME = "1r03ai111416-01_karplus_sample_app.pdf"
DOC_ID = "samples/" + PDF_NAME
DEFAULT_PDF = Path(__file__).resolve().parent / "samples" / PDF_NAME
DEFAULT_DB = "team44_shc22039_demo"
DEFAULT_COLLECTION = "past_submissions"


def text_chunks(text, chunk_words=175, overlap_words=30):
    """Produce readable overlapping word chunks; not semantic or vector chunking."""
    if chunk_words < 2 or not 0 <= overlap_words < chunk_words:
        raise ValueError("Invalid chunk window")
    # Preserve the actual words, including PDF terminology; normalize whitespace only.
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    words = [w for w in words if w]
    step = chunk_words - overlap_words
    return [" ".join(words[i:i + chunk_words]) for i in range(0, len(words), step) if words[i:i + chunk_words]]


def prepare_records(pdf_file, *, document_id=DOC_ID, collection=DEFAULT_COLLECTION):
    """Read the unchanged source PDF and produce Nina-compatible keyword-search records."""
    from pypdf import PdfReader

    pdf_file = Path(pdf_file)
    if not pdf_file.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_file}")
    digest = hashlib.sha256(pdf_file.read_bytes()).hexdigest()
    reader = PdfReader(str(pdf_file))
    records = []
    for page_i, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        for chunk_i, content in enumerate(text_chunks(page_text), start=1):
            records.append({
                "source": collection,
                "chunk_id": f"{document_id}#p{page_i:02d}-c{chunk_i:02d}",
                "text": content,
                "funding_source": "NIH",
                "doc_type": "pdf",
                "doc_link": document_id,
                "file_hash": digest,
                "uploaded_at": time.time(),
                "page_number": page_i,
                "document_title": "Modeling atomic structure of the EmrE multidrug pump to design inhibitor peptides",
                "principal_investigator": "Martin Karplus",
                "source_organization": "NIAID / Harvard University",
                "sample_notice": "Historical 2013 application; not current NIH instructions; nonprofit educational use only",
                # No 'embedding': generating vectors is Nina's heavier upload.py pipeline.
            })
    if not records:
        raise ValueError("The PDF contained no extractable text")
    return records


def import_records(db, records, *, collection=DEFAULT_COLLECTION, replace=False):
    """Idempotent import; writes only the specified document ID in one collection."""
    coll = db[collection]
    doc_id, digest = records[0]["doc_link"], records[0]["file_hash"]
    previous = coll.count_documents({"doc_link": doc_id})
    if previous:
        matching = coll.count_documents({"doc_link": doc_id, "file_hash": digest})
        if matching == previous:
            return {"status": "already_present", "chunks": previous, "doc_link": doc_id}
        if not replace:
            raise RuntimeError("An older copy of the same PDF is present. Use --replace to update only that document.")
        coll.delete_many({"doc_link": doc_id})
    coll.insert_many(records)
    return {"status": "imported", "chunks": len(records), "doc_link": doc_id}


def main():
    parser = argparse.ArgumentParser(description="Import NIAID PDF into an isolated Team 44 MongoDB database")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--uri", default=os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--database", default=os.environ.get("MONGO_DB", DEFAULT_DB))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, choices=[DEFAULT_COLLECTION])
    parser.add_argument("--dry-run", action="store_true", help="Extract and inspect; do not connect to MongoDB")
    parser.add_argument("--replace", action="store_true", help="Replace only this PDF's old chunks")
    args = parser.parse_args()
    try:
        records = prepare_records(args.pdf, collection=args.collection)
        print(f"PDF: {args.pdf.name}")
        print(f"Pages / chunks: 13 / {len(records)}")
        print(f"Example chunk: {records[0]['chunk_id']}")
        print(f"Example content: {records[0]['text'][:170]}...")
        print("Document SHA256:", records[0]["file_hash"])
        print("IMPORTANT: This fast importer stores REAL PDF text, but NO vector embeddings.")
        if args.dry_run:
            print("DRY RUN only: no database connection and no writes.")
            return 0
        from pymongo import MongoClient
        with MongoClient(args.uri, serverSelectionTimeoutMS=3500) as client:
            client.admin.command("ping")
            db = client[args.database]
            result = import_records(db, records, collection=args.collection, replace=args.replace)
            actual = db[args.collection].count_documents({"doc_link": DOC_ID})
            count_matching_emre = db[args.collection].count_documents({"doc_link": DOC_ID, "text": {"$regex": "EmrE", "$options": "i"}})
            print("Database:", args.database, " | Collection:", args.collection)
            print("Import result:", result)
            print("VERIFIED in MongoDB: chunks=", actual, ", chunks mentioning EmrE=", count_matching_emre)
            if actual <= 0 or count_matching_emre <= 0:
                print("ERROR: Database verification failed.", file=sys.stderr)
                return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
