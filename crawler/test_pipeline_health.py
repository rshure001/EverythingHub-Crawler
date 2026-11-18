import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline_health import evaluate_pipeline, evaluate_workflow

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

    def test_pipeline_reports_each_downstream_workflow_independently(self):
        result = evaluate_pipeline(
            self.payload(),
            self.payload(status="completed", conclusion="failure", run_id=11),
            now=NOW,
        )
        self.assertEqual(result["publisher"]["status"], "healthy")
        self.assertEqual(result["search_smoke"]["status"], "failed")
        self.assertEqual(result["overall_status"], "failed")

    def test_watchdog_runs_after_publisher_and_search_smoke_completion(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "crawl-watchdog.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('"EverythingHub Publisher"', workflow)
        self.assertIn('"EverythingHub search API smoke test"', workflow)
        self.assertIn("pipeline_health.py", workflow)


if __name__ == "__main__":
    unittest.main()
