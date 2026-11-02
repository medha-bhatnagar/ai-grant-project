"""Serve Medha's ORIGINAL unmodified dashboard + live MongoDB retrieval.

Unlike the previous integrated_server.py, this server has NO fake/sample mode.
A missing/unreachable MongoDB or missing imported PDF makes startup FAIL.
The only component that writes to MongoDB is import_pdf_to_mongo.py.
"""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from retrieval import retrieve_chunks
from import_pdf_to_mongo import DOC_ID, DEFAULT_DB, DEFAULT_COLLECTION, DEFAULT_PDF

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = ROOT / "dashboard" / "index.html"
WIDGET_FILE = Path(__file__).resolve().parent / "retrieval_widget_real.js"
SCRIPT = '<script src="/integration/retrieval_widget_real.js" defer></script>'


class Handler(BaseHTTPRequestHandler):
    def send_payload(self, status, data, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, info):
        self.send_payload(status, json.dumps(info, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path in ("/", "/index.html"):
            if not DASHBOARD_FILE.is_file():
                self.send_json(404, {"error": "dashboard/index.html missing from repository checkout"})
                return
            html = DASHBOARD_FILE.read_text(encoding="utf-8")
            if "</body>" not in html:
                self.send_json(500, {"error": "Cannot safely attach widget to dashboard"})
                return
            # Only injected into HTTP response; DO NOT alter the tracked HTML.
            html = html.replace("</body>", SCRIPT + "\n</body>", 1)
            self.send_payload(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if route.path == "/integration/retrieval_widget_real.js":
            self.send_payload(200, WIDGET_FILE.read_bytes(), "text/javascript; charset=utf-8")
            return
        if route.path == "/integration/original_nih_sample.pdf":
            if not DEFAULT_PDF.is_file():
                self.send_json(404, {"error": "Original PDF not found"})
                return
            self.send_payload(200, DEFAULT_PDF.read_bytes(), "application/pdf")
            return
        if route.path == "/api/status":
            try:
                count = self.server.db[DEFAULT_COLLECTION].count_documents({"doc_link": DOC_ID})
            except Exception:
                self.send_json(503, {"error": "MongoDB unavailable"})
                return
            self.send_json(200, {"mode": "real_mongodb", "collection": DEFAULT_COLLECTION, "database": self.server.db_name,
                                 "document": DOC_ID, "indexed_chunks": count,
                                 "notice": "Real historical NIH PDF, keyword search (no vectors)"})
            return
        if route.path == "/api/retrieve":
            params = parse_qs(route.query, keep_blank_values=True)
            query = params.get("query", ["EmrE"])[0].strip()
            collection = params.get("collection", [DEFAULT_COLLECTION])[0]
            source = params.get("funding_source", ["NIH"])[0]
            if collection != DEFAULT_COLLECTION or source not in ("", "NIH") or len(query) > 160:
                self.send_json(400, {"error": "Invalid query or collection"})
                return
            try:
                limit = int(params.get("limit", ["6"])[0]); assert 1 <= limit <= 20
            except (ValueError, AssertionError):
                self.send_json(400, {"error": "limit must be 1-20"})
                return
            try:
                result = retrieve_chunks(self.server.db, collection=collection, query=query,
                                         funding_source=source or None, doc_link=DOC_ID, limit=limit)
            except Exception:
                self.send_json(503, {"error": "Live MongoDB retrieval failed"})
                return
            self.send_json(200, {"mode": "real_mongodb", "query": query, "collection": collection,
                                 "count": len(result), "results": result,
                                 "source_url": "/integration/original_nih_sample.pdf"})
            return
        self.send_json(404, {"error": "Not found"})

    def log_message(self, fmt, *args):
        print("[Team44 MongoDB] " + fmt % args)


def start_server(*, port=8765, host="127.0.0.1", uri=None, database=None):
    from pymongo import MongoClient
    uri = uri or os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    db_name = database or os.environ.get("MONGO_DB", DEFAULT_DB)
    client = MongoClient(uri, serverSelectionTimeoutMS=3500)
    try:
        client.admin.command("ping")
        db = client[db_name]
        count = db[DEFAULT_COLLECTION].count_documents({"doc_link": DOC_ID})
        if count == 0:
            raise RuntimeError(f"No imported PDF found in {db_name}.{DEFAULT_COLLECTION}; run import_pdf_to_mongo.py first")
        server = ThreadingHTTPServer((host, port), Handler)
    except Exception:
        client.close()
        raise
    server.db, server.db_name, server.mongo_client = db, db_name, client
    return server


def main():
    parser = argparse.ArgumentParser(description="Live MongoDB + Medha's unchanged dashboard")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1-65535")
    try:
        srv = start_server(port=args.port)
    except Exception as exc:
        parser.exit(1, f"MongoDB integration NOT READY: {type(exc).__name__}: {exc}\n")
    print(f"LIVE MongoDB retrieval + original Medha dashboard: http://127.0.0.1:{args.port}/#pi")
    print("The original dashboard/index.html is not changed. Browser never connects to Mongo directly.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        srv.server_close()
        srv.mongo_client.close()


if __name__ == "__main__":
    main()
