import ast
import textwrap
import unittest
from pathlib import Path


class SearchApiSmokeFreshnessTests(unittest.TestCase):
    def test_live_search_smoke_verifies_first_party_catalog_preview(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("timeout-minutes: 8", workflow)
        self.assertIn('manifest_path = Path("public-search/manifest.json")', workflow)
        self.assertIn("freshness = evaluate_manifest_freshness(manifest)", workflow)
        self.assertIn('expected_pages = manifest.get("total_pages")', workflow)
        self.assertIn('expected_updated_at = manifest.get("updated_at")', workflow)
        self.assertIn('payload.get("catalogPreview")', workflow)
        self.assertIn('payload.get("catalogResults")', workflow)
        self.assertIn('"Rolex"', workflow)
        self.assertIn('"Las Vegas"', workflow)
        self.assertIn('"NASA"', workflow)
        self.assertIn('item.get("preview_only") is not True', workflow)
        self.assertIn('item.get("commercial_eligible") is not False', workflow)
        self.assertIn('item.get("source_terms_status") != "not_reviewed"', workflow)
        self.assertIn('item.get("record_level_clearance") != "not_reviewed"', workflow)
        self.assertIn('preview.get("pageIndexPages") != expected_pages', workflow)
        self.assertIn('abs(source_updated_seconds - expected_updated_at) <= 60', workflow)
        self.assertIn('expected_status = "candidate-preview" if freshness_match else "stale-candidate-preview"', workflow)
        self.assertIn('preview.get("recordsPublished") != 0', workflow)
        self.assertNotIn("VERCEL_API", workflow)
        self.assertNotIn("get_vercel", workflow)
        self.assertNotIn("  workflow_run:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  push:", workflow)
        catalog = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "retroactive-catalog-index.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", catalog)
        self.assertIn("no relevant visible page results", workflow)
        self.assertIn("unrelated derived subject catalog surfaced", workflow)

        run_block = workflow.split("run: |", 1)[1]
        script = run_block.split("python - <<'PY'", 1)[1].split("\n          PY", 1)[0]
        ast.parse(textwrap.dedent(script))

    def test_search_smoke_runs_after_catalog_analysis_or_manual_dispatch(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("  workflow_run:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  push:", workflow)
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
