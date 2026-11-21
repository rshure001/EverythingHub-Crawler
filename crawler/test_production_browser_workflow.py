from pathlib import Path
import unittest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "everything-hub-production-browser-check.yml"
)


class ProductionBrowserWorkflowContractTests(unittest.TestCase):
    def test_browser_check_runs_after_successful_catalog_publisher(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('workflows: ["Publish and verify Everything Hub — in-house Supabase route"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("vars.APPDEPLOY_ACCEPTANCE_ENABLED == 'true'", workflow)
        self.assertIn(
            "github.event_name != 'workflow_run' || "
            "github.event.workflow_run.conclusion == 'success'",
            workflow,
        )

    def test_catalog_updates_use_publisher_completion_trigger(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"public-search/**"', workflow)
        self.assertIn('vercel-homepage/**', workflow)

    def test_free_fallback_search_exposes_candidate_catalog_previews(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('#results .eh-catalog-result', workflow)
        self.assertIn('catalogPreview:{count:catalogCount', workflow)
        self.assertIn('Record rights not cleared', workflow)
        self.assertIn('Commercial use not cleared', workflow)
        self.assertIn('timeout: 25000', workflow)


if __name__ == "__main__":
    unittest.main()
