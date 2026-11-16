"""
upload.py - Read documents, split them into chunks, and save them in MongoDB.

Usage:
    python3 upload.py <folder> <collection>
    python3 upload.py Past_Submissions past_submissions
"""

import sys
from pathlib import Path

from pymongo import MongoClient
from sentence_transformers import SentenceTransformer
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
FILE_TYPES = [".pdf", ".docx", ".pptx", ".xlsx", ".html", ".md"]

# ---- Setup (runs once) ----

db = MongoClient("mongodb://localhost:27017/?directConnection=true")["grant_rag"]

# Reads documents. OCR is off because our PDFs are normal text PDFs.
pdf_options = PdfPipelineOptions(do_ocr=False)
converter = DocumentConverter(format_options={
    InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)
})

# Splits a document into chunks of at most 256 tokens.
tokenizer = HuggingFaceTokenizer.from_pretrained(model_name=MODEL_NAME, max_tokens=256)
chunker = HybridChunker(tokenizer=tokenizer)

# Turns text into embeddings (lists of 384 numbers).
embedder = SentenceTransformer(MODEL_NAME)


# ---- Upload one file ----

def upload_file(path, doc_link, collection):
    # Skip files that are already in the database
    if db[collection].find_one({"doc_link": doc_link}):
        print(f"SKIPPED  {doc_link} (already uploaded)")
        return

    # 1. Read the document
    document = converter.convert(str(path)).document

    # 2. Split it into chunks of text
    texts = [chunker.contextualize(chunk=c) for c in chunker.chunk(dl_doc=document)]

    # 3. Turn every chunk into an embedding
    embeddings = embedder.encode(texts, normalize_embeddings=True)

    # 4. Save one record per chunk
    records = []
    for i in range(len(texts)):
        records.append({
            "source": collection,
            "chunk_id": f"{doc_link}#{i}",
            "text": texts[i],
            "doc_link": doc_link,
            "funding_source": "NIH",
            "embedding": embeddings[i].tolist(),
        })

    if records:
        db[collection].insert_many(records)
    print(f"{len(records):4} chunks  {doc_link}")


# ---- Upload every file in the folder ----

folder = Path(sys.argv[1])
collection = sys.argv[2]

for path in sorted(folder.rglob("*")):
    if path.suffix.lower() not in FILE_TYPES:
        continue

    # e.g. "Past_Submissions/proposal1.pdf"
    doc_link = path.relative_to(folder.parent).as_posix()

    try:
        upload_file(path, doc_link, collection)
    except Exception as error:
        print(f"FAILED   {doc_link}: {error}")