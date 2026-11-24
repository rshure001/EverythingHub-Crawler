import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from crawler.schedule_watchdog import evaluate, recovery_dispatch_decision

NOW = datetime(2026, 11, 16, 16, 0, tzinfo=timezone.utc)


def scheduled(created_at, conclusion="success", status="completed", crawl_execution_conclusion=None):
    run = {
        "name": "Everything Hub crawler",
        "status": status,
        "conclusion": conclusion,
        "event": "schedule",
        "created_at": created_at,
    }
    if crawl_execution_conclusion is not None:
        run["crawl_execution_conclusion"] = crawl_execution_conclusion
    return run


def manifest_minutes_ago(minutes):
    return int((NOW - timedelta(minutes=minutes)).timestamp())


class ScheduleWatchdogTests(unittest.TestCase):
    def test_watchdog_schedule_is_staggered_away_from_peak_minutes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn('    - cron: "4-59/5 * * * *"', workflow)
        self.assertNotIn('    - cron: "*/5 * * * *"', workflow)

    def test_crawler_push_watches_hourly_automation_fallback_marker(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        push = workflow.split("  push:" + chr(10), 1)[1].split("  schedule:", 1)[0]
        self.assertIn('      - "crawler/automation-trigger.json"', push)

    def test_crawler_schedule_uses_five_minute_cadence(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        self.assertIn('    - cron: "2-57/5 * * * *"', workflow)
        self.assertNotIn('    - cron: "*/5 * * * *"', workflow)
        self.assertIn("every five minutes", workflow)

    def test_watchdog_runs_after_crawler_and_catalog_publishers_without_downstream_fanout(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        workflow_run = workflow.split("  workflow_run:" + chr(10), 1)[1].split("  workflow_dispatch:", 1)[0]
        workflow_names = [
            line.strip()[3:-1]
            for line in workflow_run.splitlines()
            if line.strip().startswith("- " + chr(34)) and line.strip().endswith(chr(34))
        ]
        self.assertEqual(workflow_names, [
            "Everything Hub crawler",
            "Retroactive catalog inventory",
            "Catalog-first discovery",
            "Ten-at-a-time catalog seed expansion",
        ])
        self.assertNotIn("EverythingHub isolated importer smoke test", workflow_run)
        self.assertNotIn("EverythingHub Publisher", workflow_run)
        self.assertNotIn("EverythingHub search API smoke test", workflow_run)
        self.assertNotIn("Validate Everything Hub Vercel search backend", workflow_run)
        self.assertNotIn("Validate Everything Hub crawler and schema", workflow_run)
        self.assertIn("types:" + chr(10) + "      - completed", workflow_run)
        self.assertIn("branches: [main]", workflow_run)

    def test_recent_schedule_and_fresh_manifest_are_healthy(self):
        result = evaluate(
            [scheduled("2026-11-16T15:55:00Z")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(5),
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["schedule_status"], "recent")
        self.assertEqual(result["manifest_age_seconds"], 300)

    def test_stale_schedule_and_stale_manifest_mark_once_even_after_a_recent_manual_attempt(self):
        result = evaluate(
            [
                scheduled("2026-11-16T14:00:00Z", conclusion="cancelled"),
                {"name": "Everything Hub crawler", "status": "completed", "conclusion": "success", "event": "push", "created_at": "2026-11-16T15:59:00Z"},
            ],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(150),
        )
        self.assertEqual(result["action"], "mark")
        self.assertEqual(result["schedule_status"], "stale")
        self.assertEqual(result["schedule_age_seconds"], 7200)
        self.assertEqual(result["manifest_age_seconds"], 9000)
        self.assertEqual(result["age_seconds"], 60)

    def test_active_queued_or_running_crawler_prevents_marker_update(self):
        result = evaluate(
            [
                scheduled("2026-11-16T14:00:00Z"),
                {"name": "Everything Hub crawler", "status": "queued", "event": "push", "created_at": "2026-11-16T15:59:00Z"},
                {"name": "Everything Hub crawler", "status": "in_progress", "event": "push", "created_at": "2026-11-16T15:58:00Z"},
            ],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(120),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["active_runs"], 2)

    def test_active_catalog_analysis_blocks_recovery_marker(self):
        result = evaluate(
            [
                scheduled("2026-11-16T14:00:00Z"),
                {
                    "name": "Retroactive catalog inventory",
                    "status": "in_progress",
                    "event": "schedule",
                    "created_at": "2026-11-16T15:59:00Z",
                },
            ],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(30),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["active_runs"], 1)

    def test_active_seed_expansion_blocks_recovery_marker(self):
        result = evaluate(
            [
                scheduled("2026-11-16T14:00:00Z"),
                {
                    "name": "Ten-at-a-time catalog seed expansion",
                    "status": "in_progress",
                    "event": "schedule",
                    "created_at": "2026-11-16T15:59:00Z",
                },
            ],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(30),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["active_runs"], 1)

    def test_catalog_schedule_does_not_mask_stale_crawler_schedule(self):
        result = evaluate(
            [{
                "name": "Catalog-first discovery",
                "status": "completed",
                "conclusion": "success",
                "event": "schedule",
                "created_at": "2026-11-16T15:59:00Z",
            }],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(30),
        )
        self.assertEqual(result["action"], "mark")
        self.assertEqual(result["schedule_status"], "unobserved")

    def test_stale_schedule_with_fresh_manifest_does_not_write_marker(self):
        result = evaluate(
            [scheduled("2026-11-16T14:00:00Z")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(5),
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["schedule_status"], "stale")

    def test_recent_schedule_with_stale_manifest_does_not_write_marker(self):
        result = evaluate(
            [scheduled("2026-11-16T15:55:00Z")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(60),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["schedule_status"], "recent")

    def test_unobserved_schedule_with_manifest_stale_over_10_minutes_marks_recovery(self):
        result = evaluate(
            [{"name": "Everything Hub crawler", "status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"}],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(60),
        )
        self.assertEqual(result["action"], "mark")
        self.assertEqual(result["schedule_status"], "unobserved")
        self.assertIsNone(result["schedule_age_seconds"])
        self.assertEqual(result["manifest_age_seconds"], 3600)

    def test_unobserved_schedule_with_fresh_manifest_is_healthy(self):
        result = evaluate(
            [{"name": "Everything Hub crawler", "status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"}],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(5),
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["schedule_status"], "unobserved")
        self.assertEqual(result["manifest_age_seconds"], 300)

    def test_missing_manifest_timestamp_does_not_write_marker(self):
        result = evaluate([scheduled("2026-11-16T14:00:00Z")], now=NOW)
        self.assertEqual(result["action"], "wait")
        self.assertIsNone(result["manifest_age_seconds"])

    def test_exact_10_minute_schedule_boundary_is_not_stale(self):
        result = evaluate(
            [scheduled("2026-11-16T15:50:00Z")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(30),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["schedule_age_seconds"], 600)
        self.assertEqual(result["schedule_status"], "recent")

    def test_exact_10_minute_manifest_boundary_is_not_stale(self):
        result = evaluate(
            [scheduled("2026-11-16T14:00:00Z")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(10),
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["manifest_age_seconds"], 600)

    def test_latest_scheduled_event_is_used_not_a_newer_manual_run(self):
        result = evaluate(
            [
                scheduled("2026-11-16T15:55:00Z"),
                scheduled("2026-11-16T14:00:00Z"),
                {"name": "Everything Hub crawler", "status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"},
            ],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(60),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["last_scheduled_run"], "2026-11-16T15:55:00Z")

    def test_recent_failed_scheduled_attempt_is_recent_delivery_not_a_marker(self):
        result = evaluate(
            [scheduled("2026-11-16T15:55:00Z", conclusion="failure")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(60),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["last_scheduled_conclusion"], "failure")

    def test_skipped_crawl_step_is_not_reported_as_a_successful_scheduled_crawl(self):
        result = evaluate(
            [scheduled(
                "2026-11-16T15:55:00Z",
                conclusion="success",
                crawl_execution_conclusion="skipped",
            )],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(60),
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["last_scheduled_conclusion"], "skipped")
        self.assertEqual(result["last_scheduled_workflow_conclusion"], "success")

    def test_schedule_health_is_independent_of_recent_manual_crawl(self):
        result = evaluate(
            [
                {"name": "Everything Hub crawler", "status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"},
                scheduled("2026-11-16T15:00:00Z"),
            ],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(120),
        )
        self.assertEqual(result["action"], "mark")
        self.assertEqual(result["schedule_status"], "stale")
        self.assertEqual(result["schedule_age_seconds"], 3600)

    def test_timezone_offsets_are_normalized(self):
        result = evaluate(
            [scheduled("2026-11-16T09:00:00-06:00")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(120),
        )
        self.assertEqual(result["action"], "mark")
        self.assertEqual(result["age_seconds"], 3600)

    def test_iso_manifest_timestamp_is_supported(self):
        result = evaluate(
            [scheduled("2026-11-16T15:55:00Z")],
            now=NOW,
            manifest_updated_at="2026-11-16T15:55:00Z",
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["manifest_age_seconds"], 300)

    def test_manifest_timestamp_within_clock_skew_tolerance_is_clamped_to_zero(self):
        result = evaluate(
            [scheduled("2026-11-16T15:50:00Z")],
            now=NOW,
            manifest_updated_at="2026-11-16T16:00:30Z",
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["manifest_age_seconds"], 0)

    def test_future_manifest_timestamp_beyond_clock_skew_tolerance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "too far in the future"):
            evaluate(
                [scheduled("2026-11-16T15:50:00Z")],
                now=NOW,
                manifest_updated_at="2026-11-16T16:05:00Z",
            )

    def test_invalid_manifest_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([scheduled("2026-11-16T14:00:00Z")], now=NOW, manifest_updated_at="not-a-timestamp")

    def test_recovery_dispatch_occurs_only_when_no_crawler_is_active_or_already_started(self):
        recovery_sha = "a" * 40
        result = recovery_dispatch_decision([], recovery_sha)
        self.assertEqual(result["action"], "dispatch")

        active = recovery_dispatch_decision(
            [{"name": "Everything Hub crawler", "status": "in_progress", "head_sha": "b" * 40}],
            recovery_sha,
        )
        self.assertEqual(active["action"], "wait")

        active_catalog = recovery_dispatch_decision(
            [{
                "name": "Catalog-first discovery",
                "status": "in_progress",
                "head_sha": "b" * 40,
            }],
            recovery_sha,
        )
        self.assertEqual(active_catalog["action"], "wait")

        already_started = recovery_dispatch_decision(
            [{
                "name": "Everything Hub crawler",
                "status": "completed",
                "event": "workflow_dispatch",
                "head_sha": recovery_sha,
            }],
            recovery_sha,
        )
        self.assertEqual(already_started["action"], "already_started")

        unrelated = recovery_dispatch_decision(
            [{"name": "Other workflow", "status": "in_progress", "head_sha": recovery_sha}],
            recovery_sha,
        )
        self.assertEqual(unrelated["action"], "dispatch")

    def test_recovery_dispatch_rejects_invalid_commit_sha(self):
        with self.assertRaisesRegex(ValueError, "40-character hexadecimal SHA"):
            recovery_dispatch_decision([], "not-a-sha")

    def test_workflow_rechecks_runs_manifest_and_dispatches_once_after_marker_commit(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("public-search/manifest.json", workflow)
        self.assertIn("base64.b64decode", workflow)
        self.assertIn("schedule_watchdog.py /tmp/crawl-runs-combined.json /tmp/public-manifest.json", workflow)
        self.assertIn("actions/workflows/crawl.yml/runs?event=schedule&per_page=5", workflow)
        self.assertIn("actions/runs/${latest_scheduled_run_id}/jobs?per_page=100", workflow)
        self.assertIn("crawler/automation-trigger.json", workflow)
        self.assertIn("actions/workflows/retroactive-catalog-index.yml/runs?per_page=100", workflow)
        self.assertIn("actions/workflows/catalog-first-discovery.yml/runs?per_page=100", workflow)
        self.assertIn("actions/workflows/catalog-seed-expansion.yml/runs?per_page=100", workflow)
        self.assertIn("Ten-at-a-time catalog seed expansion", workflow)
        self.assertIn("git rebase origin/main", workflow)
        self.assertIn("for attempt in 1 2 3", workflow)
        self.assertIn("git push origin HEAD:refs/heads/main", workflow)
        self.assertNotIn("--force-with-lease", workflow)
        self.assertIn("status", workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("group: eh-crawler-schedule-watchdog", workflow)
        self.assertIn("recovery_dispatch_decision", workflow)
        self.assertIn("/tmp/crawl-runs-postpush.json", workflow)
        self.assertIn("gh workflow run crawl.yml --ref main", workflow)
        self.assertNotIn("crawl.yml push path will start", workflow)

    def test_schedule_health_reports_unobserved_without_schedule_events(self):
        result = evaluate(
            [{"name": "Everything Hub crawler", "status": "completed", "conclusion": "success", "event": "workflow_dispatch", "created_at": "2026-11-16T15:59:00Z"}],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(5),
        )
        self.assertEqual(result["action"], "healthy")
        self.assertEqual(result["schedule_status"], "unobserved")
        self.assertIsNone(result["schedule_age_seconds"])



    def test_workflow_uses_manifest_age_when_schedule_history_is_unobserved(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "crawl-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("No scheduled crawl event is observable; using manifest age as a conservative fallback.", workflow)
        self.assertIn("created_at = None", workflow)
        self.assertIn("schedule_age is None and manifest_age <= 10 * 60", workflow)
        self.assertNotIn("No scheduled crawl event is observable; no recovery marker will be written.", workflow)


    def test_schedule_older_than_ten_minutes_marks_when_manifest_is_stale(self):
        result = evaluate(
            [scheduled("2026-11-16T15:49:00Z")],
            now=NOW,
            manifest_updated_at=manifest_minutes_ago(15),
        )
        self.assertEqual(result["action"], "mark")
        self.assertEqual(result["schedule_status"], "stale")
        self.assertEqual(result["schedule_age_seconds"], 660)


    def test_live_search_freshness_requests_bounded_watchdog_recovery(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "validate-vercel-search.yml").read_text(encoding="utf-8")
        self.assertIn("actions: write", workflow)
        self.assertIn("actions/workflows/crawl.yml/runs?branch=main", workflow)
        self.assertIn("actions/workflows/crawl-watchdog.yml/runs?branch=main", workflow)
        self.assertIn('gh workflow run crawl-watchdog.yml --repo "$GITHUB_REPOSITORY" --ref main', workflow)
        self.assertIn("activeCrawlerRuns", workflow)
        self.assertIn("activeWatchdogRuns", workflow)
        self.assertIn("latestSuccessfulRunAgeSeconds", workflow)
        self.assertIn("manifestAgeSeconds", workflow)
        self.assertIn("recoveryDispatchTriggered", workflow)
        self.assertNotIn("Crawler catalog timestamp is outside the 15-minute live-monitor window", workflow)

if __name__ == "__main__":
    unittest.main()
