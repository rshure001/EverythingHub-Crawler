import gzip, json, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
import ledger
from ledger import append_event, load_ledger

class ProjectLedgerTests(unittest.TestCase):
    def test_appends_once_and_rejects_duplicate_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            event = {"id":"e1","timestamp":"2026-01-01T00:00:00Z","type":"test","status":"success","summary":"Test event"}
            self.assertTrue(append_event(event, path))
            self.assertFalse(append_event(event, path))
            self.assertEqual(len(load_ledger(path)["events"]), 1)
    def test_records_compressed_catalog_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            (data / "catalogs.json.gz").write_bytes(gzip.compress(json.dumps({
                "catalog_count": 4, "datasets_discovered": 10,
                "resources_discovered": 25, "updated_at": "2026-11-20T00:00:00Z"
            }).encode("utf-8")))
            (data / "openlibrary-catalogs.json.gz").write_bytes(gzip.compress(json.dumps({
                "catalog_count": 2, "datasets_discovered": 50, "errors": []
            }).encode("utf-8")))
            (data / "universal-catalog-records.json.gz").write_bytes(gzip.compress(json.dumps({
                "metrics": {"catalog_records": 60, "unique_entities": 55,
                            "source_page_references": 60, "source_pages_fetched": 0,
                            "indexed_records": 0}
            }).encode("utf-8")))
            ledger_path = data / "project-ledger.json"
            with patch.object(ledger, "ROOT", root), patch.object(ledger, "LEDGER", ledger_path), \
                 patch.dict("os.environ", {"GITHUB_RUN_ID": "test-run"}):
                self.assertTrue(ledger.record_catalog())
                event = load_ledger(ledger_path)["events"][0]
            self.assertEqual(event["metrics"]["catalogs"], 6)
            self.assertEqual(event["metrics"]["catalog_records"], 60)
            self.assertEqual(event["metrics"]["openlibrary_records"], 50)
            self.assertEqual(event["metrics"]["source_pages_fetched"], 0)

    def test_rejects_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                append_event({"id":"e1"}, Path(directory) / "ledger.json")
    def test_rejects_duplicate_ids_in_existing_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps({"schema_version":1,"events":[{"id":"x"},{"id":"x"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_ledger(path)

if __name__ == "__main__":
    unittest.main()
