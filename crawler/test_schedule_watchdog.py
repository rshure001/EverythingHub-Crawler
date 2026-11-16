import unittest
from pathlib import Path
from datetime import datetime, timezone

from crawler.schedule_watchdog import evaluate

NOW = datetime(2026, 11, 16, 16, 0, tzinfo=timezone.utc)


class ScheduleWatchdogTests(unittest.TestCase):
    def test_watchdog_schedule_is_staggered_away_from_peak_minutes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn('    - cron: "7,22,37,52 * * * *"', workflow)
        self.assertNotIn('    - cron: "*/15 * * * *"', workflow)

    def test_watchdog_runs_after_each_completed_crawl(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn(
            '  workflow_run:\n    workflows:\n      - "Everything Hub crawler"\n    types:\n      - completed',
            workflow,
        )

    def test_recent_success_is_healthy(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:20:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["age_seconds"], 2400)

    def test_stale_success_requests_one_recovery_dispatch(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T14:00:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["age_seconds"], 7200)

    def test_active_run_prevents_duplicate_dispatch(self):
        result = evaluate(
            [
                {"status": "completed", "conclusion": "success", "created_at": "2026-11-16T12:00:00Z"},
                {"status": "queued", "conclusion": None, "created_at": "2026-11-16T15:59:00Z"},
            ],
            now=NOW,
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["active_runs"], 1)

    def test_missing_success_requests_recovery(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "failure", "created_at": "2026-11-16T15:30:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "dispatch")

    def test_latest_success_is_selected_not_first_success(self):
        result = evaluate(
            [
                {"status": "completed", "conclusion": "success", "created_at": "2026-11-16T14:00:00Z"},
                {"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:50:00Z"},
            ],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["age_seconds"], 600)

    def test_timezone_offsets_are_normalized(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T10:00:00-06:00"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["age_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
