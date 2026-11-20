import unittest
from catalog_ingest import adapt

class UniversalCatalogIngestTests(unittest.TestCase):
    def test_ckan_datasets_become_catalog_records_not_pages(self):
        result = adapt({
            "updated_at": "2026-11-20T00:00:00Z",
            "catalogs": [{"id":"test","name":"Test Data","base_url":"https://data.example.org","errors":[]}],
            "datasets": [{
                "catalog_id":"test","id":"test:roads","dataset_id":"roads","title":"Road Network",
                "description":"Roads and routes","landing_url":"https://data.example.org/dataset/roads",
                "organization":"Transport","tags":["roads","maps"],
                "resources":[{"name":"GeoJSON","format":"GEOJSON","url":"https://data.example.org/roads.geojson"}],
                "resource_count":1,"metadata_modified":"2026-11-19"
            }]
        })
        self.assertEqual(result["metrics"]["catalogs_discovered"],1)
        self.assertEqual(result["metrics"]["catalog_records"],1)
        self.assertEqual(result["metrics"]["source_pages"],1)
        self.assertEqual(result["metrics"]["indexed_records"],0)
        self.assertFalse(result["production_indexed"])
        self.assertEqual(result["records"][0]["record_type"],"dataset")
        self.assertEqual(result["records"][0]["attributes"]["resource_count"],1)

    def test_rejects_untrusted_http_landing_url(self):
        with self.assertRaises(ValueError):
            adapt({
                "catalogs":[{"id":"test","name":"Test","base_url":"https://data.example.org"}],
                "datasets":[{"catalog_id":"test","dataset_id":"x","title":"X","landing_url":"http://bad.example/x"}]
            })

if __name__ == "__main__":
    unittest.main()
