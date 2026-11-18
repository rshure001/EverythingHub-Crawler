import unittest
from pathlib import Path


class LiveDiagnosticsScheduleTests(unittest.TestCase):
    def test_scheduled_importer_completion_runs_hourly_diagnostics(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "everything-hub-live-diagnostics.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('    - cron: "17 * * * *"', workflow)
        workflow_run = workflow.split("  workflow_run:\\n", 1)[1].split(
            "jobs:\\n", 1
        )[0]
        self.assertIn('- "Everything Hub crawler"', workflow_run)
        self.assertIn('- "EverythingHub isolated importer smoke test"', workflow_run)
        self.assertIn("types:\\n      - completed", workflow_run)
        self.assertIn("branches: [main]", workflow_run)

    def test_importer_trigger_only_runs_diagnostics_for_hourly_schedule(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "everything-hub-live-diagnostics.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "if: ${{ github.event_name != 'workflow_run' || github.event.workflow_run.event == 'schedule' }}",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
