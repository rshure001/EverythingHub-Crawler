import json
import tempfile
import unittest
from pathlib import Path

from catalog_throughput import capture_baseline, record_throughput


class CatalogThroughputTests(unittest.TestCase):
    def _report(self, persisted_at, pages, eligible, catalogs, records, by_type=None):
        return {
            "generated_at": persisted_at,
            "persisted_snapshot_at": persisted_at,
            "source_index_pages": pages + 5,
            "catalogs_verified": 0,
            "records_published": 0,
            "metrics": {
                "pages_analyzed": pages,
                "eligible_pages": eligible,
                "unique_catalogs_discovered": catalogs,
                "duplicate_catalog_candidates": 10,
                "subcatalogs_discovered": 4,
                "unique_records": records,
                "records_assigned_to_catalogs": records - 2,
                "unassigned_records": 2,
                "media_references": 8,
                "unique_media_urls": 6,
                "unique_records_by_type": by_type or {"article": records},
            },
        }

    def test_capture_and_record_separate_persisted_rates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path, baseline_path = root / "report.json", root / "baseline.json"
            report_path.write_text(json.dumps(self._report("2026-11-24T22:00:00Z", 100, 90, 50, 1000)), encoding="utf-8")
            self.assertTrue(capture_baseline(report_path, baseline_path)["available"])
            report_path.write_text(json.dumps(self._report("2026-11-24T22:02:00Z", 130, 120, 56, 1120, {"article": 1120})), encoding="utf-8")
            result = record_throughput(report_path, baseline_path)
            self.assertEqual(result["throughput_status"], "measured")
            self.assertEqual(result["elapsed_wall_clock_seconds"], 120.0)
            self.assertEqual(result["count_deltas"]["pages_analyzed"], 30)
            self.assertEqual(result["count_deltas"]["unique_catalogs_discovered"], 6)
            self.assertEqual(result["count_deltas"]["unique_records"], 120)
            self.assertEqual(result["throughput"]["pages_per_minute"], 15.0)
            self.assertEqual(result["throughput"]["catalogs_per_minute"], 3.0)
            self.assertEqual(result["throughput"]["records_per_minute"], 60.0)

    def test_negative_deduplicated_counts_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path, baseline_path = root / "report.json", root / "baseline.json"
            report_path.write_text(json.dumps(self._report("2026-11-24T22:00:00Z", 100, 90, 50, 1000, {"article": 700, "product": 300})), encoding="utf-8")
            capture_baseline(report_path, baseline_path)
            report_path.write_text(json.dumps(self._report("2026-11-24T22:02:00Z", 101, 90, 49, 990, {"article": 710, "product": 280})), encoding="utf-8")
            result = record_throughput(report_path, baseline_path)
            self.assertEqual(result["count_deltas"]["unique_catalogs_discovered"], -1)
            self.assertEqual(result["count_deltas"]["unique_records"], -10)
            self.assertIn("unique_catalogs_discovered", result["negative_count_deltas"])
            self.assertIn("unique_records_by_type.product", result["negative_count_deltas"])
            self.assertEqual(result["throughput"]["catalogs_per_minute"], -0.5)
            self.assertEqual(result["throughput"]["records_per_minute"], -5.0)

    def test_missing_prior_snapshot_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path, baseline_path = root / "report.json", root / "baseline.json"
            report_path.write_text(json.dumps(self._report("2026-11-24T22:00:00Z", 100, 90, 50, 1000)), encoding="utf-8")
            baseline_path.write_text(json.dumps({"available": False, "reason": "no prior report"}), encoding="utf-8")
            result = record_throughput(report_path, baseline_path)
            self.assertEqual(result["throughput_status"], "baseline_unavailable")
            self.assertIsNone(result["throughput"])
            self.assertIsNone(result["count_deltas"])


if __name__ == "__main__":
    unittest.main()
