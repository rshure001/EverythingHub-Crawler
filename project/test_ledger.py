import json, tempfile, unittest
from pathlib import Path
from ledger import append_event, load_ledger

class ProjectLedgerTests(unittest.TestCase):
    def test_appends_once_and_rejects_duplicate_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            event = {"id":"e1","timestamp":"2026-01-01T00:00:00Z","type":"test","status":"success","summary":"Test event"}
            self.assertTrue(append_event(event, path))
            self.assertFalse(append_event(event, path))
            self.assertEqual(len(load_ledger(path)["events"]), 1)
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
