import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("bulk_ingest.py")
SPEC = importlib.util.spec_from_file_location("everythinghub_bulk_ingest", MODULE_PATH)
bulk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bulk)


class BulkIngestTests(unittest.TestCase):
    def source(self):
        return {
            "source_id": "sample.dataset",
            "name": "Sample dataset",
            "canonical_host": "data.example.org",
            "lane": "science",
            "review_status": "reviewed_allowed",
            "source_status": "active",
            "license": "CC0-1.0",
            "attribution_required": True,
            "attribution_text": "Sample dataset",
        }

    def test_normalizes_and_hashes_record(self):
        normalized = bulk.normalize(
            {"id": "rec-1", "title": "Sample", "text": "Useful content"},
            self.source(),
        )
        self.assertEqual(normalized["lane"], "science")
        self.assertEqual(normalized["source_record_id"], "rec-1")
        self.assertEqual(
            normalized["canonical_url"],
            "urn:everythinghub:source:sample.dataset:rec-1",
        )
        self.assertEqual(normalized["source_host"], "data.example.org")
        self.assertEqual(len(normalized["content_hash"]), 64)

    def test_preserves_real_canonical_url_and_host(self):
        normalized = bulk.normalize(
            {
                "id": "record-2",
                "url": "https://catalog.example.org/items/2",
                "title": "Catalog item",
            },
            self.source(),
        )
        self.assertEqual(
            normalized["canonical_url"],
            "https://catalog.example.org/items/2",
        )
        self.assertEqual(normalized["source_host"], "catalog.example.org")

    def test_requires_reviewed_source(self):
        source = self.source()
        source["review_status"] = "not_reviewed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.json"
            input_path = root / "input.jsonl"
            output_path = root / "out.jsonl"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            input_path.write_text('{"id":"1","title":"A"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not approved"):
                bulk.run(source_path, input_path, output_path, "jsonl")

    def test_deduplicates_source_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.json"
            input_path = root / "input.jsonl"
            output_path = root / "out.jsonl"
            source_path.write_text(json.dumps(self.source()), encoding="utf-8")
            rows = [
                {"id": "same", "title": "First"},
                {"id": "same", "title": "Duplicate"},
                {"id": "second", "title": "Second"},
            ]
            input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            accepted, rejected = bulk.run(source_path, input_path, output_path, "jsonl")
            self.assertEqual((accepted, rejected), (2, 1))
            staged = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(staged), 2)
            self.assertTrue(all(row["source_id"] == "sample.dataset" for row in staged))


if __name__ == "__main__":
    unittest.main()
