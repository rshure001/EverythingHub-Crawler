import inspect
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from catalog_discovery import build_catalog, normalize_dataset, SOURCES
from catalog_source_policy import policy_for_host

class CatalogDiscoveryTests(unittest.TestCase):
    def test_dataset_keeps_parent_catalog_and_bulk_resources(self):
        item = normalize_dataset({
            "name": "transport-data",
            "title": "Transport Data",
            "notes": "A public dataset",
            "organization": {"title": "Transport Agency"},
            "tags": [{"name": "transport"}],
            "license_id": "cc-by", "license_title": "Creative Commons Attribution", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "resources": [
                {"name": "CSV download", "format": "csv", "url": "https://downloads.example.org/data.csv", "size": 1200},
                {"name": "Unsafe", "format": "zip", "url": "http://downloads.example.org/data.zip"},
            ],
        }, SOURCES[0])
        self.assertEqual(item["catalog_id"], "us-data-gov")
        self.assertEqual(item["resource_count"], 1)
        self.assertEqual(item["license"]["id"], "cc-by")
        self.assertEqual(item["resources"][0]["format"], "CSV")
        self.assertTrue(item["landing_url"].startswith("https://catalog.data.gov/dataset/"))

    def test_missing_dataset_identifier_is_skipped(self):
        self.assertIsNone(normalize_dataset({"title": "No ID"}, SOURCES[0]))


    def test_default_discovery_batch_is_bounded_by_each_reviewed_host_budget(self):
        max_pages = inspect.signature(build_catalog).parameters["max_pages"].default
        self.assertEqual(max_pages, 40)
        for source in SOURCES:
            host = urlsplit(source["base"]).hostname
            policy = policy_for_host(host)
            self.assertTrue(policy["discovery_allowed"], source["id"])
            self.assertGreaterEqual(policy["max_requests_per_run"], max_pages, source["id"])
            self.assertGreaterEqual(policy["min_interval_seconds"], 1.0, source["id"])

        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "catalog-first-discovery.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('default: "40"', workflow)
        self.assertEqual(workflow.count("inputs.max_pages || '40'"), 2)

    def test_catalog_sources_are_https(self):
        self.assertTrue(all(source["base"].startswith("https://") for source in SOURCES))
        self.assertNotIn("australia-data-gov", {source["id"] for source in SOURCES})

if __name__ == "__main__":
    unittest.main()
