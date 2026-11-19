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
        start = workflow.index("  workflow_run:")
        end = workflow.index("jobs:", start)
        workflow_run = workflow[start:end]
        self.assertIn('- "Everything Hub crawler"', workflow_run)
        self.assertIn('- "EverythingHub isolated importer smoke test"', workflow_run)
        self.assertIn("- completed", workflow_run)
        self.assertIn("branches: [main]", workflow_run)

    def test_supabase_homepage_check_matches_current_edge_function_markup(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "everything-hub-live-diagnostics.yml"
        ).read_text(encoding="utf-8")
        start = workflow.index('          echo "=== HOMEPAGE ==="')
        end = workflow.index('          echo "=== CANONICAL VERCEL HOMEPAGE', start)
        homepage_check = workflow[start:end]

        for marker in (
            "'<!doctype html'",
            "'Everything Hub'",
            "'id=\"q\"'",
            "'id=\"searchForm\"'",
            "'class=\"art\"'",
            "'assets/everythinghub-approved.png'",
            "'everything-hub-media-search'",
        ):
            self.assertIn(marker, homepage_check)

        self.assertIn("grep -o 'data-mode='", homepage_check)
        self.assertIn('test "$category_count" -eq 8', homepage_check)
        self.assertNotIn("approved-master-art", homepage_check)
        self.assertNotIn('class="tile"', homepage_check)

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
