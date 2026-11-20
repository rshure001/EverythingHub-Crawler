import unittest
from pathlib import Path


class SearchApiSmokeFreshnessTests(unittest.TestCase):
    def test_live_search_smoke_waits_for_exact_committed_catalog(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("timeout-minutes: 8", workflow)
        self.assertIn("- uses: actions/checkout@v4", workflow)
        self.assertIn('manifest_path = Path("public-search/manifest.json")', workflow)
        self.assertIn("manifest_raw = manifest_path.read_bytes()", workflow)
        self.assertIn('expected_pages = manifest.get("total_pages")', workflow)
        self.assertIn('expected_updated_at = manifest.get("updated_at")', workflow)
        self.assertIn('expected_shas = {"shards": manifest_sha}', workflow)
        self.assertIn('transport = production.get("indexTransport")', workflow)
        self.assertIn("expected_sha = expected_shas.get(transport)", workflow)
        self.assertIn('production.get("indexIntegrity") == "sha256"', workflow)
        self.assertIn('production.get("indexShardCount") == manifest.get("shard_count")', workflow)
        self.assertIn("deadline = time.monotonic() + 360", workflow)
        self.assertIn("time.sleep(min(30, remaining))", workflow)
        self.assertIn('production.get("indexedPages") == expected_pages', workflow)
        self.assertIn('production.get("indexUpdatedAt") == expected_updated_at', workflow)

    def test_search_smoke_keeps_catalog_and_workflow_dispatch_paths(self):
        smoke = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "search-api-smoke.yml"
        ).read_text(encoding="utf-8")
        push_block = smoke.split("  push:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertIn('"public-search/**"', push_block)
        self.assertIn('workflows: ["EverythingHub Publisher"]', smoke)

        crawl = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "crawl.yml"
        ).read_text(encoding="utf-8")
        dispatch = crawl.split("Dispatch catalog publishing and search verification", 1)[1]
        self.assertIn("gh workflow run publish-site.yml", dispatch)
        self.assertIn("gh workflow run search-api-smoke.yml", dispatch)
        self.assertIn("search smoke test; it waits for the committed catalog", dispatch)


if __name__ == "__main__":
    unittest.main()
