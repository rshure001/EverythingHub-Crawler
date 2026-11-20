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


    def test_records_page_derived_catalog_metrics_and_persisted_throughput(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "data" / "page-derived-catalogs"
            report_dir.mkdir(parents=True)
            report = {
                "generated_at": "2026-11-20T22:00:00Z",
                "source_index_pages": 131899,
                "source_index_sha256": "a" * 64,
                "records_published": 0,
                "catalogs_verified": 0,
                "previous_counts": {"pages_analyzed": 90, "unique_catalogs_discovered": 7, "unique_records": 30},
                "count_deltas": {"pages_analyzed": 10, "unique_catalogs_discovered": 1, "unique_records": 10},
                "elapsed_wall_clock_seconds": 600,
                "analysis_elapsed_seconds": 12.5,
                "throughput": {
                    "pages_per_minute": 1.0,
                    "catalogs_per_minute": 0.1,
                    "records_per_minute": 1.0,
                    "measurement": "persisted_count_delta_over_wall_clock_interval",
                },
                "metrics": {
                    "pages_analyzed": 100,
                    "eligible_pages": 95,
                    "unique_catalogs_discovered": 8,
                    "duplicate_catalog_candidates": 12,
                    "subcatalogs_discovered": 2,
                    "unique_records": 40,
                    "unique_records_by_type": {"article": 20, "product": 20},
                    "media_references": 15,
                    "unique_media_urls": 10,
                    "records_staged": 40,
                    "source_terms_reviewed_records": 0,
                    "commercially_eligible_records": 0,
                    "records_with_provenance": 40,
                    "records_with_timestamps": 40,
                    "cross_host_record_merges": 1,
                    "catalogs_with_records": 6,
                    "catalogs_without_records": 2,
                    "unassigned_records": 4,
                    "coverage_percentage": 99.0,
                    "unique_source_coverage_percentage": 100.0,
                    "eligible_page_coverage_percentage": 95.0,
                    "eligible_unique_source_page_coverage_percentage": 95.0,
                },
            }
            (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            ledger_path = root / "data" / "project-ledger.json"
            with patch.object(ledger, "ROOT", root), patch.object(ledger, "LEDGER", ledger_path), \
                 patch.dict("os.environ", {"GITHUB_RUN_ID": "analysis-run"}):
                self.assertTrue(ledger.record_page_derived_catalog_analysis())
                event = load_ledger(ledger_path)["events"][0]
                self.assertFalse(ledger.record_page_derived_catalog_analysis())
            self.assertEqual(event["type"], "page_derived_catalog_analysis")
            self.assertEqual(event["status"], "validated_staging")
            self.assertEqual(event["metrics"]["unique_catalogs_discovered"], 8)
            self.assertEqual(event["metrics"]["unique_records"], 40)
            self.assertEqual(event["metrics"]["records_published"], 0)
            self.assertEqual(event["metrics"]["throughput"]["catalogs_per_minute"], 0.1)
            self.assertEqual(event["metrics"]["elapsed_wall_clock_seconds"], 600)
            self.assertEqual(event["source"]["source_index_sha256"], "a" * 64)

    def test_rejects_malformed_page_derived_catalog_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "data" / "page-derived-catalogs"
            report_dir.mkdir(parents=True)
            (report_dir / "report.json").write_text(json.dumps({
                "generated_at": "2026-11-20T22:00:00Z",
                "source_index_pages": 10,
                "source_index_sha256": "bad",
                "metrics": {},
            }), encoding="utf-8")
            with patch.object(ledger, "ROOT", root), patch.object(ledger, "LEDGER", root / "data" / "project-ledger.json"):
                with self.assertRaises(ValueError):
                    ledger.record_page_derived_catalog_analysis()

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
