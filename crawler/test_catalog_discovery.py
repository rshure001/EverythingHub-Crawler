import unittest

from catalog_discovery import normalize_dataset, SOURCES

class CatalogDiscoveryTests(unittest.TestCase):
    def test_dataset_keeps_parent_catalog_and_bulk_resources(self):
        item = normalize_dataset({
            "name": "transport-data",
            "title": "Transport Data",
            "notes": "A public dataset",
            "organization": {"title": "Transport Agency"},
            "tags": [{"name": "transport"}],
            "resources": [
                {"name": "CSV download", "format": "csv", "url": "https://downloads.example.org/data.csv", "size": 1200},
                {"name": "Unsafe", "format": "zip", "url": "http://downloads.example.org/data.zip"},
            ],
        }, SOURCES[0])
        self.assertEqual(item["catalog_id"], "us-data-gov")
        self.assertEqual(item["resource_count"], 1)
        self.assertEqual(item["resources"][0]["format"], "CSV")
        self.assertTrue(item["landing_url"].startswith("https://catalog.data.gov/dataset/"))

    def test_missing_dataset_identifier_is_skipped(self):
        self.assertIsNone(normalize_dataset({"title": "No ID"}, SOURCES[0]))

    def test_catalog_sources_are_https(self):
        self.assertTrue(all(source["base"].startswith("https://") for source in SOURCES))

if __name__ == "__main__":
    unittest.main()
