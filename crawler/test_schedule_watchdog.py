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

    def test_crawler_schedule_uses_staggered_ten_minute_cadence(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        self.assertIn('    - cron: "3,13,23,33,43,53 * * * *"', workflow)
        self.assertNotIn('    - cron: "*/5 * * * *"', workflow)
        self.assertIn("every 10 minutes", workflow)

    def test_watchdog_runs_after_crawler_validation_importer_release_and_independent_search_completion(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        workflow_run = workflow.split("  workflow_run:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertEqual(workflow_run.count('- "'), 6)
        self.assertIn('- "Everything Hub crawler"', workflow_run)
        self.assertNotIn('- "Everything Hub live diagnostics"', workflow_run)
        self.assertIn('- "EverythingHub isolated importer smoke test"', workflow_run)
        self.assertIn('- "EverythingHub Publisher"', workflow_run)
        self.assertIn('- "EverythingHub search API smoke test"', workflow_run)
        self.assertIn('- "Validate Everything Hub Vercel search backend"', workflow_run)
        self.assertIn('- "Validate Everything Hub crawler and schema"', workflow_run)
        self.assertIn("types:\n      - completed", workflow_run)
        self.assertIn("branches: [main]", workflow_run)

    def test_recent_success_is_healthy(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:55:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["age_seconds"], 300)

    def test_stale_success_requests_one_recovery_dispatch(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T14:00:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["age_seconds"], 7200)

    def test_success_older_than_ten_minutes_requests_recovery(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:49:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["age_seconds"], 660)

    def test_recent_failure_waits_before_retry(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "failure", "created_at": "2026-11-16T15:55:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["age_seconds"], 300)

    def test_stale_failure_requests_one_recovery_dispatch(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "failure", "created_at": "2026-11-16T14:00:00Z"}],
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

    def test_no_crawl_history_requests_recovery(self):
        result = evaluate([], now=NOW)
        self.assertEqual(result["action"], "dispatch")
        self.assertIsNone(result["age_seconds"])

    def test_recent_failure_is_not_overridden_by_an_older_success(self):
        result = evaluate(
            [
                {"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:50:00Z"},
                {"status": "completed", "conclusion": "failure", "created_at": "2026-11-16T15:55:00Z"},
            ],
            now=NOW,
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["last_conclusion"], "failure")

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

    def test_recent_success_with_fresh_manifest_is_healthy(self):
        manifest_time = int(datetime(2026, 11, 16, 15, 55, tzinfo=timezone.utc).timestamp())
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:55:00Z"}],
            now=NOW,
            manifest_updated_at=manifest_time,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["age_seconds"], 300)
        self.assertEqual(result["manifest_age_seconds"], 300)

    def test_recent_success_with_stale_manifest_waits_before_retry(self):
        manifest_time = int(datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc).timestamp())
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:50:00Z"}],
            now=NOW,
            manifest_updated_at=manifest_time,
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["manifest_age_seconds"], 7200)

    def test_stale_success_with_stale_manifest_requests_recovery(self):
        manifest_time = int(datetime(2026, 11, 16, 13, 30, tzinfo=timezone.utc).timestamp())
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T14:00:00Z"}],
            now=NOW,
            manifest_updated_at=manifest_time,
        )
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["manifest_age_seconds"], 9000)

    def test_iso_manifest_timestamp_is_supported(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:55:00Z"}],
            now=NOW,
            manifest_updated_at="2026-11-16T15:55:00Z",
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["manifest_age_seconds"], 300)

    def test_manifest_timestamp_within_clock_skew_tolerance_is_clamped_to_zero(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:50:00Z"}],
            now=NOW,
            manifest_updated_at="2026-11-16T16:00:30Z",
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["manifest_age_seconds"], 0)

    def test_future_manifest_timestamp_beyond_clock_skew_tolerance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "too far in the future"):
            evaluate(
                [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T15:50:00Z"}],
                now=NOW,
                manifest_updated_at="2026-11-16T16:05:00Z",
            )

    def test_invalid_manifest_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([], now=NOW, manifest_updated_at="not-a-timestamp")

    def test_workflow_passes_the_public_manifest_to_the_watchdog(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("public-search/manifest.json", workflow)
        self.assertIn("base64.b64decode", workflow)
        self.assertIn("schedule_watchdog.py /tmp/crawl-runs-combined.json /tmp/public-manifest.json", workflow)
        self.assertIn("actions/workflows/crawl.yml/runs?event=schedule&per_page=5", workflow)
        self.assertIn("Schedule trigger status", workflow)

    def test_schedule_health_is_independent_of_recent_manual_crawl(self):
        result = evaluate(
            [
                {"status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"},
                {"status": "completed", "conclusion": "success", "event": "schedule", "created_at": "2026-11-16T15:00:00Z"},
            ],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["schedule_status"], "stale")
        self.assertEqual(result["schedule_age_seconds"], 3600)
        self.assertEqual(result["last_scheduled_conclusion"], "success")

    def test_recent_schedule_delivery_is_reported_separately(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "event": "schedule", "created_at": "2026-11-16T15:52:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["schedule_status"], "recent")
        self.assertEqual(result["schedule_age_seconds"], 480)

    def test_failed_scheduled_attempt_shows_recent_delivery_but_waits(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "failure", "event": "schedule", "created_at": "2026-11-16T15:55:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["schedule_status"], "recent")
        self.assertEqual(result["last_scheduled_conclusion"], "failure")

    def test_missing_schedule_history_is_unobserved_not_healthy(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["schedule_status"], "unobserved")
        self.assertIsNone(result["schedule_age_seconds"])

    def test_timezone_offsets_are_normalized(self):
        result = evaluate(
            [{"status": "completed", "conclusion": "success", "created_at": "2026-11-16T10:00:00-06:00"}],
            now=NOW,
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["age_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
