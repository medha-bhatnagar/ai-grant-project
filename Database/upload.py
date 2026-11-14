"""
uploader.py - Chunk, embed and store documents in MongoDB.

Example:
    from pymongo import MongoClient
    from uploader import DocumentUploader

    db = MongoClient("mongodb://localhost:27017")["grant_rag"]
    up = DocumentUploader(db)                     # loads models once

    up.upload_file("Announcements/Document1.pdf", "announcements")
    up.upload_folder("Past_Submissions", "past_submissions", funding_source="NIH")
"""

import hashlib
import time
from pathlib import Path

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
FILE_TYPES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md"}


class DocumentUploader:
    def __init__(self, db, embed_model=DEFAULT_MODEL, max_tokens=256, device=None):
        """
        db          a pymongo Database
        embed_model Hugging Face sentence-transformers model name
        max_tokens  maximum tokens per chunk
        device      "cpu", "cuda", or None for automatic
        """
        from docling.chunking import HybridChunker
        from docling.document_converter import DocumentConverter
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
        from sentence_transformers import SentenceTransformer
        from transformers import AutoTokenizer

        self.db = db
        self.embed_model = embed_model
        self.converter = DocumentConverter()
        self.chunker = HybridChunker(tokenizer=HuggingFaceTokenizer(
            tokenizer=AutoTokenizer.from_pretrained(embed_model),
            max_tokens=max_tokens,
        ))
        self.model = SentenceTransformer(embed_model, device=device)

    def upload_file(self, path, collection, funding_source="NIH", doc_id=None,
                    extra_fields=None, skip_unchanged=True):
        """
        Chunk, embed and store one file.

        path            file to upload
        collection      collection name (str) or a pymongo Collection
        funding_source  saved on every chunk
        doc_id          stable ID for the file (default: the path as given).
                        Re-uploading with the same doc_id replaces the old chunks.
        extra_fields    dict of additional fields saved on every chunk
        skip_unchanged  if True, do nothing when the same file content is already stored

        Returns the number of chunks stored (0 if skipped or no text was found).
        Raises on conversion or database errors.
        """
        path = Path(path)
        coll = self.db[collection] if isinstance(collection, str) else collection
        doc_id = doc_id or path.as_posix()
        digest = _file_hash(path)

        if skip_unchanged and coll.find_one({"doc_link": doc_id, "file_hash": digest}, {"_id": 1}):
            return 0

        doc = self.converter.convert(str(path)).document
        pieces = [self.chunker.contextualize(chunk=c) for c in self.chunker.chunk(dl_doc=doc)]
        if not pieces:
            return 0
        vectors = self.model.encode(pieces, batch_size=32)

        now = time.time()
        records = [{
            "source": coll.name,
            "chunk_id": f"{doc_id}#{i}",
            "text": piece,
            "funding_source": funding_source,
            "doc_type": path.suffix.lower().lstrip("."),
            "doc_link": doc_id,
            "file_hash": digest,
            "embedding": vec.tolist(),
            "embed_model": self.embed_model,
            "uploaded_at": now,
            **(extra_fields or {}),
        } for i, (piece, vec) in enumerate(zip(pieces, vectors))]

        coll.delete_many({"doc_link": doc_id})      # replace an older version, if any
        coll.insert_many(records)
        return len(records)

    def upload_folder(self, folder, collection, recursive=True, **kwargs):
        """
        Upload every supported file in a folder. doc_id for each file is its
        path relative to the folder's parent, e.g. "Announcements/Document1.pdf".

        Returns {file_path: chunks_stored or Exception}. One bad file doesn't
        stop the rest.
        """
        folder = Path(folder)
        pattern = "**/*" if recursive else "*"
        results = {}
        for f in sorted(folder.glob(pattern)):
            if not f.is_file() or f.suffix.lower() not in FILE_TYPES:
                continue
            try:
                doc_id = f.relative_to(folder.parent).as_posix()
                results[str(f)] = self.upload_file(f, collection, doc_id=doc_id, **kwargs)
            except Exception as e:
                results[str(f)] = e
        return results

    def delete_document(self, doc_id, collection):
        """Remove all chunks of one document. Returns the number deleted."""
        coll = self.db[collection] if isinstance(collection, str) else collection
        return coll.delete_many({"doc_link": doc_id}).deleted_count


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()