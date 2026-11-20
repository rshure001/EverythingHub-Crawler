import unittest
from datetime import datetime, timezone
from pathlib import Path

from crawler.pipeline_health import (
    evaluate_deploy_job,
    evaluate_manifest_freshness,
    evaluate_pipeline,
    evaluate_workflow,
)

NOW = datetime(2026, 11, 18, 21, 10, tzinfo=timezone.utc)


class PipelineHealthTests(unittest.TestCase):
    def payload(self, status="completed", conclusion="success", created_at="2026-11-18T21:05:00Z", run_id=10):
        return {"workflow_runs": [{
            "id": run_id, "run_number": run_id, "status": status,
            "conclusion": conclusion, "created_at": created_at,
        }]}

    def test_recent_success_is_healthy(self):
        result = evaluate_workflow(self.payload(), "publisher", now=NOW)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["age_seconds"], 300)

    def test_old_success_is_stale(self):
        result = evaluate_workflow(
            self.payload(created_at="2026-11-18T20:00:00Z"), "publisher", now=NOW
        )
        self.assertEqual(result["status"], "stale")

    def test_failed_latest_run_is_not_hidden_by_older_success(self):
        payload = {"workflow_runs": [
            {"id": 9, "status": "completed", "conclusion": "success", "created_at": "2026-11-18T21:04:00Z"},
            {"id": 10, "status": "completed", "conclusion": "failure", "created_at": "2026-11-18T21:05:00Z"},
        ]}
        self.assertEqual(evaluate_workflow(payload, "publisher", now=NOW)["status"], "failed")

    def test_active_run_is_reported_as_running(self):
        result = evaluate_workflow(
            self.payload(status="in_progress", conclusion=None), "search_smoke", now=NOW
        )
        self.assertEqual(result["status"], "running")

    def test_missing_history_is_unobserved(self):
        result = evaluate_workflow({"workflow_runs": []}, "publisher", now=NOW)
        self.assertEqual(result["status"], "unobserved")

    def test_invalid_payload_fails_closed(self):
        with self.assertRaises(ValueError):
            evaluate_workflow({}, "publisher", now=NOW)

    def test_skipped_pages_deploy_is_reported_as_blocked_not_live(self):
        result = evaluate_deploy_job({"jobs": [
            {"name": "validate-and-build", "status": "completed", "conclusion": "success"},
            {"name": "deploy", "status": "completed", "conclusion": "skipped"},
        ]})
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["conclusion"], "skipped")

    def test_successful_deploy_job_is_still_unverified(self):
        result = evaluate_deploy_job({"jobs": [
            {"name": "deploy", "status": "completed", "conclusion": "success"},
        ]})
        self.assertEqual(result["status"], "job_succeeded_unverified")

    def test_missing_deploy_job_is_unobserved(self):
        self.assertEqual(evaluate_deploy_job({"jobs": []})["status"], "unobserved")

    def test_pipeline_reports_build_deploy_and_search_independently(self):
        result = evaluate_pipeline(
            self.payload(),
            {"jobs": [{"name": "deploy", "status": "completed", "conclusion": "skipped"}]},
            self.payload(run_id=11),
            now=NOW,
        )
        self.assertEqual(result["publisher"]["status"], "healthy")
        self.assertEqual(result["deploy_job"]["status"], "skipped")
        self.assertEqual(result["search_smoke"]["status"], "healthy")
        self.assertEqual(result["overall_status"], "blocked")

    def test_downstream_health_is_checked_on_schedule_not_every_workflow_completion(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "crawl-watchdog.yml"
        ).read_text(encoding="utf-8")
        workflow_run = workflow.split("  workflow_run:" + chr(10), 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertEqual(workflow_run.count('- "'), 1)
        self.assertIn('- "Everything Hub crawler"', workflow_run)
        self.assertNotIn("EverythingHub Publisher", workflow_run)
        self.assertNotIn("EverythingHub search API smoke test", workflow_run)
        self.assertNotIn("github.event.workflow_run.name", workflow)
        self.assertIn('    - cron: "7,22,37,52 * * * *"', workflow)
        self.assertIn("pipeline_health.py", workflow)

    def test_search_manifest_at_fifteen_minute_boundary_is_fresh(self):
        updated_at = int(datetime(2026, 11, 18, 20, 55, tzinfo=timezone.utc).timestamp())
        result = evaluate_manifest_freshness({"updated_at": updated_at}, now=NOW)
        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["age_seconds"], 900)

    def test_search_manifest_older_than_fifteen_minutes_is_stale(self):
        updated_at = int(datetime(2026, 11, 18, 20, 54, 59, tzinfo=timezone.utc).timestamp())
        result = evaluate_manifest_freshness({"updated_at": updated_at}, now=NOW)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["age_seconds"], 901)

    def test_search_manifest_small_future_clock_skew_is_clamped(self):
        updated_at = int(datetime(2026, 11, 18, 21, 10, 30, tzinfo=timezone.utc).timestamp())
        result = evaluate_manifest_freshness({"updated_at": updated_at}, now=NOW)
        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["age_seconds"], 0)

    def test_search_manifest_future_timestamp_beyond_tolerance_is_rejected(self):
        updated_at = int(datetime(2026, 11, 18, 21, 11, 1, tzinfo=timezone.utc).timestamp())
        with self.assertRaisesRegex(ValueError, "too far in the future"):
            evaluate_manifest_freshness({"updated_at": updated_at}, now=NOW)

    def test_search_manifest_invalid_timestamps_fail_closed(self):
        for updated_at in ("invalid", True, 0, -1, None):
            with self.subTest(updated_at=updated_at):
                with self.assertRaises(ValueError):
                    evaluate_manifest_freshness({"updated_at": updated_at}, now=NOW)

    def test_search_api_smoke_enforces_manifest_freshness(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("from crawler.pipeline_health import evaluate_manifest_freshness", workflow)
        self.assertIn("freshness = evaluate_manifest_freshness(manifest)", workflow)
        self.assertIn('if freshness["status"] != "fresh":', workflow)


if __name__ == "__main__":
    unittest.main()
