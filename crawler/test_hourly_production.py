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
        self.assertEqual(interval["metric_kind"], "pages")
        self.assertEqual(interval["before_pages"], 100)
        self.assertEqual(interval["after_pages"], 140)
        self.assertEqual(interval["new_unique_pages"], 40)
        self.assertEqual(interval["elapsed_seconds"], 600)
        self.assertEqual(interval["run_duration_seconds"], 100.0)
        self.assertEqual(interval["pages_per_minute"], 24.0)
        self.assertEqual(interval["pages_per_hour"], 1440.0)
        self.assertEqual(interval["wall_clock_pages_per_minute"], 4.0)
        self.assertEqual(interval["wall_clock_pages_per_hour"], 240.0)
        self.assertEqual(result["metric_kind"], "pages")
        self.assertEqual(result["runs"][-1]["indexed_pages_processed"], 500)
        self.assertNotEqual(
            result["runs"][-1]["indexed_pages_processed"],
            interval["new_unique_pages"],
        )

    def test_missing_run_duration_does_not_publish_a_unique_rate(self):
        before_time = self.now - timedelta(minutes=5)
        result = build_snapshot(
            {"pages_indexed_this_run": 50},
            now=self.now,
            run_id="no-duration",
            manifest_before=self.manifest(100, before_time),
            manifest_after=self.manifest(120, self.now),
        )
        interval = result["latest_manifest_interval"]
        self.assertTrue(interval["valid"])
        self.assertEqual(interval["new_unique_pages"], 20)
        self.assertIsNone(interval["pages_per_minute"])
        self.assertEqual(interval["rate_reason"], "crawler_run_duration_unavailable")
        self.assertEqual(interval["wall_clock_pages_per_minute"], 4.0)

    def test_missing_manifest_pair_never_falls_back_to_run_upserts(self):
        result = build_snapshot(
            {"pages_indexed_this_run": 300, "run_duration_seconds": 60},
            now=self.now,
            run_id="unmeasured",
        )

        interval = result["latest_manifest_interval"]
        self.assertFalse(interval["valid"])
        self.assertIsNone(interval["new_unique_pages"])
        self.assertEqual(interval["reason"], "manifest_baseline_unavailable")
        self.assertEqual(result["runs"][-1]["indexed_pages_processed"], 300)

    def test_manifest_shrink_is_reported_as_negative_net_change(self):
        before_time = self.now - timedelta(minutes=5)
        result = build_snapshot(
            {"pages_indexed_this_run": 20, "run_duration_seconds": 300},
            now=self.now,
            run_id="shrink",
            manifest_before=self.manifest(200, before_time),
            manifest_after=self.manifest(190, self.now),
        )

        interval = result["latest_manifest_interval"]
        self.assertTrue(interval["valid"])
        self.assertEqual(interval["new_unique_pages"], -10)
        self.assertEqual(interval["pages_per_minute"], -2.0)
        self.assertEqual(interval["pages_per_hour"], -120.0)
        self.assertEqual(interval["wall_clock_pages_per_minute"], -2.0)

    def test_rerun_replaces_same_run_instead_of_double_counting(self):
        history = {"runs": [{
            "run_id": "same",
            "timestamp": (self.now - timedelta(minutes=10)).isoformat(),
            "indexed_pages_processed": 200,
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
        self.assertEqual(result["latest_manifest_interval"]["new_unique_pages"], 25)
        self.assertEqual(result["recent_run_activity"]["run_count"], 1)

    def test_old_history_is_pruned(self):
        history = {"runs": [{
            "run_id": "old",
            "timestamp": (self.now - timedelta(hours=25)).isoformat(),
            "indexed_pages_processed": 9999,
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
        self.assertEqual(result["latest_manifest_interval"]["new_unique_pages"], 10)


    def test_legacy_history_metrics_are_migrated_as_pages(self):
        legacy_time = self.now - timedelta(minutes=5)
        history = {"runs": [{
            "run_id": "legacy",
            "timestamp": legacy_time.isoformat(),
            "indexed_records_processed": 200,
            "fetch_attempts": 250,
            "duration_seconds": 90,
            "manifest_interval": {
                "valid": True,
                "new_unique_records": 20,
                "records_per_minute": 13.33,
                "records_per_hour": 800.0,
                "wall_clock_records_per_minute": 4.0,
                "wall_clock_records_per_hour": 240.0,
            },
        }]}
        result = build_snapshot(
            {"pages_indexed_this_run": 10, "fetch_attempts_this_run": 12,
             "run_duration_seconds": 60},
            history,
            now=self.now,
            run_id="fresh",
            manifest_before=self.manifest(100, self.now - timedelta(minutes=2)),
            manifest_after=self.manifest(110, self.now),
        )

        legacy = next(row for row in result["runs"] if row["run_id"] == "legacy")
        self.assertEqual(legacy["indexed_pages_processed"], 200)
        self.assertNotIn("indexed_records_processed", legacy)
        self.assertEqual(legacy["metric_kind"], "pages")
        self.assertEqual(legacy["manifest_interval"]["new_unique_pages"], 20)
        self.assertEqual(legacy["manifest_interval"]["pages_per_minute"], 13.33)
        self.assertNotIn("new_unique_records", legacy["manifest_interval"])
        self.assertNotIn("records_per_minute", legacy["manifest_interval"])
        self.assertEqual(result["recent_run_activity"]["pages_processed"], 210)
        self.assertNotIn("records_processed", result["recent_run_activity"])



if __name__ == "__main__":
    unittest.main()
