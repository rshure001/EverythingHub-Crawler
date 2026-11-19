import unittest
from datetime import datetime, timezone, timedelta

from hourly_production import build_snapshot


class HourlyProductionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def test_records_sum_across_runs_in_last_hour(self):
        history = {"runs": [{
            "run_id": "old",
            "timestamp": (self.now - timedelta(minutes=60)).isoformat(),
            "indexed_records": 600,
            "fetch_attempts": 700,
            "duration_seconds": 120,
        }]}
        result = build_snapshot(
            {"pages_indexed_this_run": 500, "fetch_attempts_this_run": 600, "run_duration_seconds": 100},
            history, now=self.now, run_id="new",
        )
        metrics = result["rolling_60m"]
        self.assertEqual(metrics["indexed_records"], 1100)
        self.assertEqual(metrics["fetch_attempts"], 1300)
        self.assertTrue(metrics["full_60_minute_window"])
        self.assertTrue(metrics["target_met"])

    def test_partial_window_reports_pace_without_claiming_full_hour(self):
        result = build_snapshot(
            {"pages_indexed_this_run": 300, "run_duration_seconds": 60},
            now=self.now, run_id="one",
        )
        metrics = result["rolling_60m"]
        self.assertFalse(metrics["full_60_minute_window"])
        self.assertEqual(metrics["indexed_records"], 300)
        self.assertTrue(metrics["target_met"])
        self.assertLess(metrics["coverage_seconds"], 3600)

    def test_rerun_replaces_same_run_instead_of_double_counting(self):
        history = {"runs": [{
            "run_id": "same",
            "timestamp": (self.now - timedelta(minutes=10)).isoformat(),
            "indexed_records": 200,
            "fetch_attempts": 250,
            "duration_seconds": 90,
        }]}
        result = build_snapshot(
            {"pages_indexed_this_run": 225, "fetch_attempts_this_run": 260, "run_duration_seconds": 95},
            history, now=self.now, run_id="same",
        )
        self.assertEqual(result["rolling_60m"]["indexed_records"], 225)
        self.assertEqual(result["rolling_60m"]["run_count"], 1)

    def test_old_history_is_pruned(self):
        history = {"runs": [{
            "run_id": "old",
            "timestamp": (self.now - timedelta(hours=25)).isoformat(),
            "indexed_records": 9999,
            "duration_seconds": 100,
        }]}
        result = build_snapshot(
            {"pages_indexed_this_run": 10, "run_duration_seconds": 30},
            history, now=self.now, run_id="fresh",
        )
        self.assertEqual(result["rolling_60m"]["indexed_records"], 10)
        self.assertEqual(len(result["runs"]), 1)


if __name__ == "__main__":
    unittest.main()
