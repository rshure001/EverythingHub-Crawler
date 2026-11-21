import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PostCrawlDispatchTests(unittest.TestCase):
    def test_first_party_search_smoke_waits_for_catalog_analysis(self):
        crawler = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        self.assertNotIn("  actions: write", crawler)
        self.assertNotIn("gh workflow run search-api-smoke.yml --ref main", crawler)
        self.assertNotIn("gh workflow run publish-site.yml --ref main", crawler)
        self.assertIn("  workflow_run:", search)
        self.assertIn('workflows: ["Retroactive catalog inventory"]', search)
        self.assertIn("types: [completed]", search)
        self.assertIn("branches: [main]", search)


    def test_crawler_job_has_time_for_bounded_crawl_and_commit(self):
        workflow = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        match = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", workflow, re.MULTILINE)
        self.assertIsNotNone(match, "crawler job timeout must be explicit")
        self.assertGreaterEqual(int(match.group(1)), 15)

    def test_publisher_and_search_smoke_support_explicit_dispatch(self):
        for path in (".github/workflows/publish-site.yml", ".github/workflows/search-api-smoke.yml", ".github/workflows/recovery-search-check.yml"):
            workflow = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("  workflow_dispatch:", workflow)

        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_run:", search)
        self.assertIn('workflows: ["Retroactive catalog inventory"]', search)
        self.assertIn("  workflow_dispatch:", search)
        self.assertNotIn("EverythingHub Publisher", search)

    def test_search_smoke_waits_for_catalog_workflow_completion(self):
        publisher = (ROOT / ".github" / "workflows" / "publish-site.yml").read_text(encoding="utf-8")
        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        publisher_triggers = publisher.split("permissions:", 1)[0]
        self.assertIn("  workflow_dispatch:", publisher_triggers)
        self.assertNotIn("  push:", publisher_triggers)
        self.assertNotIn('      - "public-search/**"', publisher_triggers)
        self.assertNotIn('      - "publisher/**"', publisher_triggers)
        search_triggers = search.split("permissions:", 1)[0]
        self.assertIn("  workflow_run:", search_triggers)
        self.assertIn('workflows: ["Retroactive catalog inventory"]', search_triggers)
        self.assertIn("types: [completed]", search_triggers)
        self.assertIn("branches: [main]", search_triggers)
        self.assertIn("  workflow_dispatch:", search_triggers)
        self.assertNotIn("  push:", search_triggers)
        self.assertNotIn('      - "public-search/**"', search_triggers)
        self.assertNotIn(".github/workflows/publish-site.yml", publisher)


if __name__ == "__main__":
    unittest.main()
