import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PostCrawlDispatchTests(unittest.TestCase):
    def test_crawler_dispatches_publisher_and_search_after_catalog_commit(self):
        workflow = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        commit_step = workflow.index("      - name: Commit updated index and frontier")
        dispatch_step = workflow.index("      - name: Dispatch catalog publishing and search verification")
        self.assertLess(commit_step, dispatch_step)
        self.assertIn("  actions: write", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("gh workflow run publish-site.yml --ref main", workflow)
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", workflow)

    def test_publisher_and_search_smoke_support_explicit_dispatch(self):
        for path in (".github/workflows/publish-site.yml", ".github/workflows/search-api-smoke.yml", ".github/workflows/recovery-search-check.yml"):
            workflow = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("  workflow_dispatch:", workflow)

        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        self.assertNotIn("  workflow_run:", search)

    def test_search_smoke_push_trigger_is_limited_to_catalog_artifacts(self):
        publisher = (ROOT / ".github" / "workflows" / "publish-site.yml").read_text(encoding="utf-8")
        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        self.assertIn('      - "public-search/**"', publisher)
        self.assertIn('      - "publisher/**"', publisher)
        self.assertIn('      - "public-search/manifest.json"', search)
        self.assertIn('      - "public-search/shard-*.json.gz"', search)
        self.assertNotIn('      - "public-search/**"', search)
        self.assertNotIn('      - "supabase/functions/everything-hub-web-search/**"', search)
        self.assertNotIn('      - ".github/workflows/search-api-smoke.yml"', search)
        self.assertNotIn(".github/workflows/publish-site.yml", publisher)


if __name__ == "__main__":
    unittest.main()
