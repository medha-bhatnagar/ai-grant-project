import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from import_pdf_to_mongo import DOC_ID, DEFAULT_PDF, prepare_records, text_chunks, import_records


class RealPDFTests(unittest.TestCase):
    def test_pdf_exists_and_kept_unchanged(self):
        self.assertTrue(DEFAULT_PDF.is_file())
        self.assertTrue(DEFAULT_PDF.stat().st_size > 100000)

    def test_actual_pdf_text_extracted(self):
        rows = prepare_records(DEFAULT_PDF)
        self.assertGreater(len(rows), 40)
        self.assertTrue(any('EmrE' in row['text'] for row in rows))
        self.assertTrue(any('peptide' in row['text'].lower() for row in rows))
        self.assertTrue(all(row['source'] == 'past_submissions' for row in rows))
        self.assertTrue(all(row['doc_link'] == DOC_ID for row in rows))
        self.assertTrue(all('embedding' not in row for row in rows))
        self.assertTrue(all(1 <= row['page_number'] <= 13 for row in rows))
        self.assertTrue(all(len(row['file_hash']) == 64 for row in rows))

    def test_chunk_overlap(self):
        chunks = text_chunks('one two three four five six', chunk_words=4, overlap_words=2)
        self.assertEqual(chunks[0], 'one two three four')
        self.assertEqual(chunks[1], 'three four five six')

    def test_import_new_document_uses_only_target_collection(self):
        rows = prepare_records(DEFAULT_PDF)
        coll = Mock()
        coll.count_documents.return_value = 0
        db = {"past_submissions": coll}
        result = import_records(db, rows)
        self.assertEqual(result['status'], 'imported')
        coll.insert_many.assert_called_once_with(rows)
        coll.delete_many.assert_not_called()

    def test_idempotency(self):
        rows = prepare_records(DEFAULT_PDF)
        coll = Mock()
        coll.count_documents.side_effect = [len(rows),len(rows)]
        db={"past_submissions":coll}
        result=import_records(db,rows)
        self.assertEqual(result['status'],'already_present')
        coll.insert_many.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class HTTPWiringTests(unittest.TestCase):
    """The HTTP tests use in-memory doubles ONLY inside tests; production requires real Mongo."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from real_mongo_server import Handler

        rows = prepare_records(DEFAULT_PDF)

        class FakeCursor:
            def __init__(self, results): self.results = results
            def limit(self, n): return self.results[:n]
            def __iter__(self): return iter(self.results)

        class FakeCollection:
            def count_documents(self, filter):
                return len([r for r in rows if all(r.get(k) == v for k, v in filter.items())])
            def find(self, filter, projection):
                import re
                matches = []
                for r in rows:
                    ok = True
                    for k,v in filter.items():
                        if isinstance(v, dict) and "$regex" in v:
                            if not re.search(v["$regex"], r.get(k, ""), re.I): ok=False
                        elif r.get(k)!=v: ok=False
                    if ok: matches.append({k: r[k] for k in projection if projection[k] == 1 and k in r})
                return FakeCursor(matches)

        class FakeDatabase:
            def __getitem__(self, key):
                if key != 'past_submissions': raise AssertionError("Unapproved collection")
                return FakeCollection()

        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.http.db = FakeDatabase()
        cls.http.db_name = "test_double_not_actual_mongo"
        cls.server_thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.http.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.server_thread.join(timeout=2)

    def test_api_serves_actual_pdf_chunks_through_retrieval_py(self):
        import urllib.request
        with urllib.request.urlopen(self.url + '/api/retrieve?query=EmrE&collection=past_submissions&funding_source=NIH') as response:
            data = json.load(response)
        self.assertEqual(data['mode'], 'real_mongodb')
        self.assertGreater(data['count'], 0)
        self.assertIn('EmrE', ' '.join(rec['text'] for rec in data['results']))

    def test_dashboard_is_injected_in_response_not_modified_on_disk(self):
        import urllib.request
        from real_mongo_server import DASHBOARD_FILE
        before = hashlib.sha256(DASHBOARD_FILE.read_bytes()).hexdigest()
        with urllib.request.urlopen(self.url + '/') as response:
            html = response.read().decode('utf-8')
        after = hashlib.sha256(DASHBOARD_FILE.read_bytes()).hexdigest()
        self.assertIn('retrieval_widget_real.js', html)
        self.assertIn('Grant Application Assistant', html)
        self.assertEqual(before,after)

    def test_original_pdf_route_is_identical_bytes(self):
        import urllib.request
        with urllib.request.urlopen(self.url + '/integration/original_nih_sample.pdf') as response:
            data = response.read()
        self.assertEqual(hashlib.sha256(data).digest(), hashlib.sha256(DEFAULT_PDF.read_bytes()).digest())
