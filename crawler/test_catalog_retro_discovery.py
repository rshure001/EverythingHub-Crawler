import unittest

from catalog_retro_discovery import build_candidates, host_key

class RetroCatalogDiscoveryTests(unittest.TestCase):
    def test_groups_existing_pages_by_normalized_host(self):
        result = build_candidates({"total_pages": 3, "pages": [
            {"url": "https://www.example.com/catalog", "title": "Product Catalog", "lane": "shopping"},
            {"url": "https://example.com/api/items", "title": "API directory", "lane": "web"},
            {"url": "https://other.example.org/about", "title": "About", "lane": "web"},
        ]})
        self.assertEqual(result["hosts_discovered"], 2)
        example = next(x for x in result["host_candidates"] if x["host"] == "example.com")
        self.assertEqual(example["source_pages"], 2)
        self.assertGreater(example["hint_counts"]["catalog"], 0)
        self.assertGreater(example["hint_counts"]["api"], 0)

    def test_counts_are_separate_and_traceable(self):
        result = build_candidates({"total_pages": 1, "pages": [
            {"url": "https://shop.example/products", "title": "Products"}
        ]})
        self.assertEqual(result["source_index_pages"], 1)
        self.assertEqual(result["metrics"]["source_pages_grouped"], 1)
        self.assertEqual(result["metrics"]["candidate_hosts"], 1)

    def test_every_page_is_preserved_as_a_discovery_seed_not_a_fake_catalog(self):
        result = build_candidates({"total_pages": 2, "pages": [
            {"url": "https://example.com/products", "title": "Product Catalog"},
            {"url": "https://example.com/about", "title": "About us"},
        ]})
        self.assertEqual(result["metrics"]["pages_as_discovery_seeds"], 2)
        self.assertEqual(len(result["page_catalog_seeds"]), 2)
        self.assertTrue(all(x["status"] == "needs_page_catalog_extraction" for x in result["page_catalog_seeds"]))
        self.assertEqual(result["metrics"]["pages_with_catalog_signals"], 1)
        self.assertGreaterEqual(result["metrics"]["page_catalog_signal_references"], 1)

    def test_rejects_credential_urls_and_normalizes_www(self):
        self.assertEqual(host_key("https://www.example.com/a"), "example.com")
        self.assertIsNone(host_key("https://user:pass@example.com/"))

if __name__ == "__main__":
    unittest.main()
