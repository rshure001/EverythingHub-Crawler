import unittest
from datetime import datetime, timezone, timedelta

from hourly_production import build_snapshot


class HourlyProductionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def manifest(self, pages, stamp):
        return {
            "total_pages": pages,
            "updated_at": int(stamp.timestamp()),
        }

    def test_unique_growth_comes_from_before_after_manifest_counts(self):
        before_time = self.now - timedelta(minutes=10)
        result = build_snapshot(
            {
                "pages_indexed_this_run": 500,
                "fetch_attempts_this_run": 600,
                "run_duration_seconds": 100,
            },
            now=self.now,
            run_id="new",
            manifest_before=self.manifest(100, before_time),
            manifest_after=self.manifest(140, self.now),
        )

        interval = result["latest_manifest_interval"]
        self.assertTrue(interval["valid"])
        self.assertEqual(interval["before_pages"], 100)
        self.assertEqual(interval["after_pages"], 140)
        self.assertEqual(interval["new_unique_records"], 40)
        self.assertEqual(interval["elapsed_seconds"], 600)
        self.assertEqual(interval["records_per_minute"], 4.0)
        self.assertEqual(interval["records_per_hour"], 240.0)
        self.assertEqual(result["runs"][-1]["indexed_records_processed"], 500)
        self.assertNotEqual(
            result["runs"][-1]["indexed_records_processed"],
            interval["new_unique_records"],
        )

    def test_missing_manifest_pair_never_falls_back_to_run_upserts(self):
        result = build_snapshot(
            {"pages_indexed_this_run": 300, "run_duration_seconds": 60},
            now=self.now,
            run_id="unmeasured",
        )

        interval = result["latest_manifest_interval"]
        self.assertFalse(interval["valid"])
        self.assertIsNone(interval["new_unique_records"])
        self.assertEqual(interval["reason"], "manifest_baseline_unavailable")
        self.assertEqual(result["runs"][-1]["indexed_records_processed"], 300)

    def test_manifest_shrink_is_reported_as_negative_net_change(self):
        before_time = self.now - timedelta(minutes=5)
        result = build_snapshot(
            {"pages_indexed_this_run": 20},
            now=self.now,
            run_id="shrink",
            manifest_before=self.manifest(200, before_time),
            manifest_after=self.manifest(190, self.now),
        )

        interval = result["latest_manifest_interval"]
        self.assertTrue(interval["valid"])
        self.assertEqual(interval["new_unique_records"], -10)
        self.assertEqual(interval["records_per_minute"], -2.0)
        self.assertEqual(interval["records_per_hour"], -120.0)

    def test_rerun_replaces_same_run_instead_of_double_counting(self):
        history = {"runs": [{
            "run_id": "same",
            "timestamp": (self.now - timedelta(minutes=10)).isoformat(),
            "indexed_records_processed": 200,
            "fetch_attempts": 250,
            "duration_seconds": 90,
        }]}
        before_time = self.now - timedelta(minutes=5)
        result = build_snapshot(
            {"pages_indexed_this_run": 225, "fetch_attempts_this_run": 260},
            history,
            now=self.now,
            run_id="same",
            manifest_before=self.manifest(100, before_time),
            manifest_after=self.manifest(125, self.now),
        )

        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["latest_manifest_interval"]["new_unique_records"], 25)
        self.assertEqual(result["recent_run_activity"]["run_count"], 1)

    def test_old_history_is_pruned(self):
        history = {"runs": [{
            "run_id": "old",
            "timestamp": (self.now - timedelta(hours=25)).isoformat(),
            "indexed_records_processed": 9999,
            "duration_seconds": 100,
        }]}
        before_time = self.now - timedelta(minutes=2)
        result = build_snapshot(
            {"pages_indexed_this_run": 10, "run_duration_seconds": 30},
            history,
            now=self.now,
            run_id="fresh",
            manifest_before=self.manifest(100, before_time),
            manifest_after=self.manifest(110, self.now),
        )

        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["latest_manifest_interval"]["new_unique_records"], 10)


if __name__ == "__main__":
    unittest.main()
