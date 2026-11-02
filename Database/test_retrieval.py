"""Offline tests for retrieval.py. No MongoDB connection is required."""
import unittest
from unittest.mock import MagicMock
from retrieval import retrieve_chunks


class RetrievalTests(unittest.TestCase):
    def test_filters_and_no_embeddings_in_projection(self):
        db = MagicMock()
        cursor = db["announcements"].find.return_value
        cursor.limit.return_value = iter([{"chunk_id": "a#0", "text": "budget rules"}])
        got = retrieve_chunks(db, query="Budget (NIH)", funding_source="NIH", limit=3)
        self.assertEqual(got, [{"chunk_id": "a#0", "text": "budget rules"}])
        filters, projection = db["announcements"].find.call_args.args
        self.assertEqual(filters["text"]["$regex"], r"Budget\ \(NIH\)")
        self.assertEqual(filters["funding_source"], "NIH")
        self.assertEqual(projection["embedding"] if "embedding" in projection else None, None)
        self.assertEqual(projection["_id"], 0)
        cursor.limit.assert_called_once_with(3)

    def test_limit_is_bounded(self):
        db = MagicMock()
        db["announcements"].find.return_value.limit.return_value = iter([])
        self.assertEqual(retrieve_chunks(db, limit=1000), [])
        db["announcements"].find.return_value.limit.assert_called_once_with(50)


if __name__ == "__main__":
    unittest.main()
