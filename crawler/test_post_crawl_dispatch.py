import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PostCrawlDispatchTests(unittest.TestCase):
    def test_crawler_dispatches_live_search_only_after_catalog_commit(self):
        workflow = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        commit_step = workflow.index("      - name: Commit updated index and frontier")
        dispatch_step = workflow.index("      - name: Dispatch live search verification (no GitHub Pages deployment)")
        self.assertLess(commit_step, dispatch_step)
        self.assertIn("  actions: write", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertNotIn("gh workflow run publish-site.yml --ref main", workflow)
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", workflow)


    def test_crawler_job_has_time_for_commit_and_post_commit_dispatch(self):
        workflow = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        match = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", workflow, re.MULTILINE)
        self.assertIsNotNone(match, "crawler job timeout must be explicit")
        self.assertGreaterEqual(int(match.group(1)), 15)

    def test_publisher_and_search_smoke_support_explicit_dispatch(self):
        for path in (".github/workflows/publish-site.yml", ".github/workflows/search-api-smoke.yml", ".github/workflows/recovery-search-check.yml"):
            workflow = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("  workflow_dispatch:", workflow)

        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        self.assertNotIn("  workflow_run:", search)
        self.assertNotIn("EverythingHub Publisher", search)

    def test_search_smoke_push_trigger_is_limited_to_catalog_artifacts(self):
        publisher = (ROOT / ".github" / "workflows" / "publish-site.yml").read_text(encoding="utf-8")
        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        publisher_triggers = publisher.split("permissions:", 1)[0]
        self.assertIn("  workflow_dispatch:", publisher_triggers)
        self.assertNotIn("  push:", publisher_triggers)
        self.assertNotIn('      - "public-search/**"', publisher_triggers)
        self.assertNotIn('      - "publisher/**"', publisher_triggers)
        self.assertIn('      - "public-search/manifest.json"', search)
        self.assertIn('      - "public-search/shard-*.json.gz"', search)
        self.assertNotIn('      - "public-search/**"', search)
        self.assertNotIn('      - "supabase/functions/everything-hub-web-search/**"', search)
        self.assertNotIn('      - ".github/workflows/search-api-smoke.yml"', search)
        self.assertNotIn(".github/workflows/publish-site.yml", publisher)


if __name__ == "__main__":
    unittest.main()
