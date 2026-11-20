import gzip
import json
import tempfile
import unittest
from pathlib import Path

from catalog_page_analysis import analyze_pages, search_catalog_network, write_outputs, validate_outputs


class PageDerivedCatalogAnalysisTests(unittest.TestCase):
    def test_generic_page_does_not_become_a_fake_catalog(self):
        result = analyze_pages([{
            "url": "https://example.org/about",
            "title": "About us",
            "description": "Information about this organization.",
            "text": "Contact and background information.",
            "lane": "general",
            "robots_allowed": True,
        }], source_total=1)
        self.assertEqual(result["metrics"]["pages_analyzed"], 1)
        self.assertEqual(result["metrics"]["eligible_pages"], 1)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)
        self.assertEqual(result["metrics"]["unique_records"], 0)
        self.assertEqual(result["metrics"]["records_published"], 0)

    def test_rolex_model_pages_form_one_deduplicated_catalog(self):
        pages = [
            {
                "url": "https://www.rolex.com/watches/submariner?utm_source=one",
                "title": "Submariner",
                "description": "Rolex watch model",
                "text": "Discover the Rolex Submariner watch collection and its features.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 100,
            },
            {
                "url": "https://rolex.com/watches/day-date",
                "title": "Day-Date",
                "description": "Rolex watch model",
                "text": "Explore the Rolex Day-Date watch and its details.",
                "lane": "shopping", "robots_allowed": True, "fetched_at": 101,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"model": 2})
        self.assertEqual(result["catalogs"][0]["title"], "Rolex Models")
        hits = search_catalog_network("Rolex", result)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["record_count"], 2)
        self.assertEqual(len(hits[0]["matched_records"]), 2)

    def test_las_vegas_place_pages_form_a_catalog(self):
        pages = [
            {
                "url": "https://visitlasvegas.com/places/las-vegas/strip",
                "title": "Las Vegas Strip",
                "description": "Explore the Las Vegas Strip.",
                "text": "Places and attractions in Las Vegas.",
                "lane": "places", "robots_allowed": True,
            },
            {
                "url": "https://visitlasvegas.com/places/las-vegas/downtown",
                "title": "Downtown Las Vegas",
                "description": "Explore downtown Las Vegas.",
                "text": "Places, attractions and destinations in Las Vegas.",
                "lane": "places", "robots_allowed": True,
            },
        ]
        result = analyze_pages(pages, source_total=2)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["unique_records_by_type"], {"place": 2})
        hits = search_catalog_network("Las Vegas", result)
        self.assertTrue(hits)
        self.assertIn("Las Vegas", hits[0]["title"])

    def test_one_page_can_support_multiple_distinct_catalog_candidates(self):
        result = analyze_pages([{
            "url": "https://visitlasvegas.com/places/las-vegas/events",
            "title": "Las Vegas Events",
            "description": "Browse events and places in Las Vegas.",
            "text": "Browse events and places across Las Vegas.",
            "lane": "places", "robots_allowed": True,
        }], source_total=1)
        self.assertGreaterEqual(result["metrics"]["unique_catalogs_discovered"], 2)
        self.assertEqual(result["metrics"]["pages_analyzed"], 1)

    def test_robots_denied_pages_are_not_eligible_or_catalog_sources(self):
        result = analyze_pages([{
            "url": "https://example.org/products",
            "title": "Products",
            "description": "Browse all products.",
            "text": "Catalog of products.",
            "lane": "shopping", "robots_allowed": False,
        }], source_total=1)
        self.assertEqual(result["metrics"]["eligible_pages"], 0)
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)

    def test_registry_and_per_catalog_indexes_validate_after_round_trip(self):
        pages = [
            {"url": "https://shop.example/products/a", "title": "Product A", "text": "Product details and specifications.", "lane": "shopping"},
            {"url": "https://shop.example/products/b", "title": "Product B", "text": "Product details and specifications.", "lane": "shopping"},
        ]
        result = analyze_pages(pages, source_total=2)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest = write_outputs(result, output)
            verified = validate_outputs(output)
            registry = json.loads(gzip.decompress((output / "registry.json.gz").read_bytes()))
            self.assertEqual(manifest["catalog_count"], 1)
            self.assertEqual(verified["pages_analyzed"], 2)
            self.assertEqual(len(registry["catalogs"]), 1)
            index_file = registry["catalogs"][0]["index_file"]
            self.assertTrue((output / index_file).exists())
            self.assertEqual(registry["catalogs"][0]["record_count"], 2)
            self.assertTrue(list((output / "indexes").glob("*.json.gz")))


if __name__ == "__main__":
    unittest.main()
