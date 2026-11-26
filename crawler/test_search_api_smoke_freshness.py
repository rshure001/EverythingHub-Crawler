import ast
import textwrap
import unittest
from pathlib import Path


class SearchApiSmokeFreshnessTests(unittest.TestCase):
    def test_live_search_smoke_verifies_independent_fallback_and_catalog_deferral(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("timeout-minutes: 8", workflow)
        self.assertIn('independent = get("everything-hub-independent-search", "Rolex")', workflow)
        self.assertIn('independent.get("provider") != "Bing RSS"', workflow)
        self.assertIn('independent.get("independentOfDatabase") is not True', workflow)
        self.assertIn('for query in ("Rolex", "Las Vegas", "NASA")', workflow)
        self.assertIn('payload.get("catalogPreview")', workflow)
        self.assertIn('payload.get("catalogResults")', workflow)
        self.assertIn('preview.get("status") != "deferred"', workflow)
        self.assertIn('preview.get("previewOnly") is not True', workflow)
        self.assertIn('preview.get("recordsPublished") != 0', workflow)
        self.assertIn('not isinstance(catalogs, list) or catalogs', workflow)
        self.assertNotIn("VERCEL_API", workflow)
        self.assertNotIn("get_vercel", workflow)
        self.assertNotIn("  workflow_run:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("  push:", workflow)
        self.assertIn('      - "supabase/functions/everything-hub-web-search/**"', workflow)
        catalog = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "retroactive-catalog-index.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", catalog)

        run_block = workflow.split("run: |", 1)[1]
        script = run_block.split("python - <<'PY'", 1)[1].split("\n          PY", 1)[0]
        ast.parse(textwrap.dedent(script))

    def test_search_smoke_runs_on_search_code_changes_or_manual_dispatch(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("  workflow_run:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("  push:", workflow)
        self.assertIn('      - "supabase/functions/everything-hub-web-search/**"', workflow)
        catalog = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "retroactive-catalog-index.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", catalog)
        self.assertNotIn("EverythingHub Publisher", workflow)


if __name__ == "__main__":
    unittest.main()
