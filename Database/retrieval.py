"""Read document chunks stored by Team 44's Database/upload.py.

This module performs read-only MongoDB queries. It does not upload files,
insert records, update records, or create collections.
"""

import argparse
import json
import os
import re


FIELDS = {
    "_id": 0,
    "chunk_id": 1,
    "text": 1,
    "source": 1,
    "doc_link": 1,
    "funding_source": 1,
    "doc_type": 1,
    "uploaded_at": 1,
}


def retrieve_chunks(db, collection="announcements", query=None,
                    funding_source=None, doc_link=None, limit=5):
    """Return a list of matching document chunks, ready for a JSON API.

    `db` is a pymongo Database (e.g., client["grant_rag"]). `query` is a
    case-insensitive literal substring match, not semantic/vector search.
    `collection` should be chosen by trusted application code.
    """
    filters = {}
    if query and query.strip():
        filters["text"] = {"$regex": re.escape(query.strip()), "$options": "i"}
    if funding_source:
        filters["funding_source"] = funding_source
    if doc_link:
        filters["doc_link"] = doc_link

    limit = max(1, min(int(limit), 50))
    cursor = db[collection].find(filters, FIELDS).limit(limit)
    return list(cursor)


def main():
    parser = argparse.ArgumentParser(description="Read Team 44 MongoDB document chunks")
    parser.add_argument("--query", help="Case-insensitive text substring")
    parser.add_argument("--collection", default="announcements")
    parser.add_argument("--funding-source")
    parser.add_argument("--doc-link")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--database", default=os.getenv("MONGO_DB", "grant_rag"))
    args = parser.parse_args()

    from pymongo import MongoClient

    uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    with MongoClient(uri, serverSelectionTimeoutMS=3000) as client:
        rows = retrieve_chunks(
            client[args.database], collection=args.collection,
            query=args.query, funding_source=args.funding_source,
            doc_link=args.doc_link, limit=args.limit,
        )
    print(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
