import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PostCrawlDispatchTests(unittest.TestCase):
    def test_first_party_search_smoke_runs_after_validated_catalog_publish(self):
        crawler = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        catalog = (ROOT / ".github" / "workflows" / "retroactive-catalog-index.yml").read_text(encoding="utf-8")
        self.assertIn("  actions: write", crawler)
        self.assertIn("gh workflow run crawl.yml --ref main", crawler)
        self.assertNotIn("gh workflow run search-api-smoke.yml --ref main", crawler)
        self.assertNotIn("gh workflow run publish-site.yml --ref main", crawler)
        self.assertNotIn("  workflow_run:", search)
        self.assertIn("  workflow_dispatch:", search)
        self.assertIn("python crawler/publish_catalog_snapshot.py --attempts 8", catalog)
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", catalog)
        self.assertLess(catalog.index("Validate registry and every per-catalog index before candidate publication"), catalog.index("Publish candidate catalog snapshot only after integrity and dedup validation"))


    def test_catalog_expansion_is_chained_after_successful_retroactive_inventory(self):
        workflow = (ROOT / ".github" / "workflows" / "catalog-seed-expansion.yml").read_text(encoding="utf-8")
        self.assertIn('  workflow_run:', workflow)
        self.assertIn('      - "Retroactive catalog inventory"', workflow)
        self.assertIn('      - completed', workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("python crawler/catalog_seed_expansion.py --limit 10", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

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
        self.assertNotIn("  workflow_run:", search)
        self.assertIn("  workflow_dispatch:", search)
        catalog = (ROOT / ".github" / "workflows" / "retroactive-catalog-index.yml").read_text(encoding="utf-8")
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", catalog)
        self.assertNotIn("EverythingHub Publisher", search)

    def test_search_smoke_runs_on_search_code_changes_and_after_catalog_publication(self):
        publisher = (ROOT / ".github" / "workflows" / "publish-site.yml").read_text(encoding="utf-8")
        search = (ROOT / ".github" / "workflows" / "search-api-smoke.yml").read_text(encoding="utf-8")
        catalog = (ROOT / ".github" / "workflows" / "retroactive-catalog-index.yml").read_text(encoding="utf-8")
        publisher_triggers = publisher.split("permissions:", 1)[0]
        self.assertIn("  workflow_dispatch:", publisher_triggers)
        self.assertNotIn("  push:", publisher_triggers)
        self.assertNotIn('      - "public-search/**"', publisher_triggers)
        self.assertNotIn('      - "publisher/**"', publisher_triggers)
        search_triggers = search.split("permissions:", 1)[0]
        self.assertNotIn("  workflow_run:", search_triggers)
        self.assertIn("  workflow_dispatch:", search_triggers)
        self.assertIn("  push:", search_triggers)
        self.assertIn('      - "supabase/functions/everything-hub-web-search/**"', search_triggers)
        self.assertIn("gh workflow run search-api-smoke.yml --ref main", catalog)
        publish_block = catalog.split("- name: Publish candidate catalog snapshot only after integrity and dedup validation", 1)[1]
        publish_position = publish_block.index("python crawler/publish_catalog_snapshot.py --attempts 8")
        dispatch_position = publish_block.index("gh workflow run search-api-smoke.yml --ref main")
        self.assertLess(publish_position, dispatch_position)
        self.assertNotIn('      - "public-search/**"', search_triggers)
        self.assertNotIn(".github/workflows/publish-site.yml", publisher)


if __name__ == "__main__":
    unittest.main()
