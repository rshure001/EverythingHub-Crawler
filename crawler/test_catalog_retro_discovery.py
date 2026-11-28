import unittest
from pathlib import Path

from catalog_retro_discovery import build_candidates, host_key


class RetroCatalogDiscoveryTests(unittest.TestCase):
    def test_groups_existing_pages_by_normalized_host(self):
        result = build_candidates(
            {
                "total_pages": 3,
                "pages": [
                    {"url": "https://www.example.com/catalog", "title": "Product Catalog", "lane": "shopping"},
                    {"url": "https://example.com/api/items", "title": "API directory", "lane": "web"},
                    {"url": "https://other.example.org/about", "title": "About", "lane": "web"},
                ],
            }
        )
        self.assertEqual(result["hosts_discovered"], 2)
        example = next(x for x in result["host_candidates"] if x["host"] == "example.com")
        self.assertEqual(example["source_pages"], 2)
        self.assertGreater(example["hint_counts"]["catalog"], 0)
        self.assertGreater(example["hint_counts"]["api"], 0)

    def test_counts_are_separate_and_traceable(self):
        result = build_candidates(
            {"total_pages": 1, "pages": [{"url": "https://shop.example/products", "title": "Products"}]}
        )
        self.assertEqual(result["source_index_pages"], 1)
        self.assertEqual(result["metrics"]["unique_source_pages"], 1)
        self.assertEqual(result["metrics"]["source_pages_grouped"], 1)
        self.assertEqual(result["metrics"]["candidate_hosts"], 1)
        self.assertEqual(result["metrics"]["unique_catalog_candidates"], 0)

    def test_only_signal_pages_become_analysis_work_items_not_catalogs(self):
        result = build_candidates(
            {
                "total_pages": 2,
                "pages": [
                    {"url": "https://example.com/products", "title": "Product Catalog"},
                    {"url": "https://example.com/about", "title": "About us"},
                ],
            }
        )
        items = result["page_analysis_work_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(result["metrics"]["pages_as_discovery_work_items"], 1)
        self.assertEqual(result["metrics"]["pages_with_catalog_signals"], 1)
        self.assertEqual(result["metrics"]["page_analysis_work_items_created"], 1)
        self.assertEqual(result["metrics"]["unique_catalog_candidates"], 0)
        self.assertEqual(result["metrics"]["verified_page_catalogs"], 0)
        self.assertTrue(items[0]["work_item_id"].startswith("page-analysis:"))
        self.assertEqual(items[0]["analysis_status"], "pending_page_content_analysis")
        self.assertTrue(items[0]["catalog_signals"])
        for forbidden in (
            "catalog_id",
            "catalog_seed_id",
            "catalog_type",
            "catalog_status",
            "catalog_origin",
            "verified",
            "initial_record_count",
            "related_catalog_ids",
        ):
            self.assertNotIn(forbidden, items[0])

    def test_pages_without_catalog_signals_create_no_catalog_placeholder(self):
        result = build_candidates(
            {"total_pages": 1, "pages": [{"url": "https://example.com/about", "title": "About us"}]}
        )
        self.assertEqual(result["page_analysis_work_items"], [])
        self.assertEqual(result["metrics"]["page_analysis_work_items_created"], 0)
        self.assertEqual(result["metrics"]["unique_catalog_candidates"], 0)
        self.assertEqual(result["metrics"]["verified_page_catalogs"], 0)

    def test_duplicate_source_urls_create_only_one_work_item(self):
        url = "https://example.com/products"
        result = build_candidates(
            {
                "total_pages": 2,
                "pages": [
                    {"url": url, "title": "Products"},
                    {"url": url, "title": "Products duplicate row"},
                ],
            }
        )
        self.assertEqual(len(result["page_analysis_work_items"]), 1)
        self.assertEqual(result["metrics"]["pages_as_discovery_work_items"], 1)
        self.assertEqual(result["metrics"]["duplicate_source_page_rows_skipped"], 1)
        self.assertEqual(len({x["work_item_id"] for x in result["page_analysis_work_items"]}), 1)

    def test_rejects_credential_urls_and_normalizes_www(self):
        self.assertEqual(host_key("https://www.example.com/a"), "example.com")
        self.assertIsNone(host_key("https://user:pass@example.com/"))

    def test_whole_catalog_discovery_serializes_with_main_branch_data_writers(self):
        root = Path(__file__).resolve().parents[1]
        catalog_first = (root / ".github" / "workflows" / "catalog-first-discovery.yml").read_text(encoding="utf-8")
        page_derived = (root / ".github" / "workflows" / "retroactive-catalog-index.yml").read_text(encoding="utf-8")
        self.assertIn("group: catalog-first-discovery-", catalog_first)
        self.assertIn("group: everythinghub-main-writers-${{ github.ref }}", page_derived)
        self.assertIn('      - "crawler/automation-trigger.json"', page_derived)
        self.assertIn("timeout-minutes: 240", page_derived)
        self.assertIn("timeout-minutes: 225", page_derived)
        self.assertIn("timeout-minutes: 90", catalog_first)

    def test_full_catalog_inventory_runs_hourly_not_after_every_crawler_cycle(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "retroactive-catalog-index.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('schedule:\n    - cron: "23 * * * *"', workflow)
        self.assertNotIn("workflow_run:", workflow)
        self.assertNotIn("Everything Hub crawler", workflow)
        self.assertIn("cancel-in-progress:", workflow)

    def test_inventory_cache_checkpoint_is_saved_after_cancelled_analysis(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "retroactive-catalog-index.yml"
        ).read_text(encoding="utf-8")
        analysis_step = workflow.split(
            "      - name: Analyze every eligible existing indexed page", 1
        )[1].split(
            "      - name: Save resumable per-host catalog cache checkpoint", 1
        )[0]
        checkpoint_step = workflow.split(
            "      - name: Save resumable per-host catalog cache checkpoint", 1
        )[1].split(
            "      - name: Record persisted catalog and record throughput", 1
        )[0]
        self.assertIn("--cache-dir data/.page-derived-catalog-cache", analysis_step)
        self.assertIn("        if: always()", checkpoint_step)
        self.assertNotIn("hashFiles", checkpoint_step)
        self.assertIn("uses: actions/cache/save@v4", checkpoint_step)
        self.assertIn("data/.page-derived-catalog-cache", checkpoint_step)


if __name__ == "__main__":
    unittest.main()
