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

    def test_merges_library_archive_catalogs_and_document_records(self):
        result = adapt({
            "updated_at": "2026-11-20T00:00:00Z",
            "catalogs": [{
                "id": "openlibrary-subject-science",
                "name": "Open Library — Science",
                "base_url": "https://openlibrary.org",
                "api_path": "/subjects/science.json",
                "category": "science",
                "catalog_type": "library_archive",
                "coverage_status": "partial",
                "errors": [],
            }],
            "datasets": [{
                "catalog_id": "openlibrary-subject-science",
                "id": "openlibrary-subject-science:OL123W",
                "dataset_id": "OL123W",
                "title": "A Science Book",
                "landing_url": "https://openlibrary.org/works/OL123W",
                "record_type": "document",
                "canonical_entity_key": "openlibrary:work:OL123W",
                "subject": "science",
                "attributes": {"edition_count": 4},
            }],
        })
        self.assertEqual(result["metrics"]["catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["catalog_records"], 1)
        self.assertEqual(result["records"][0]["record_type"], "document")
        self.assertEqual(result["records"][0]["attributes"]["edition_count"], 4)
        self.assertEqual(result["catalogs"][0]["catalog_type"], "library_archive")

    def test_rejects_untrusted_http_landing_url(self):
        with self.assertRaises(ValueError):
            adapt({
                "catalogs":[{"id":"test","name":"Test","base_url":"https://data.example.org"}],
                "datasets":[{"catalog_id":"test","dataset_id":"x","title":"X","landing_url":"http://bad.example/x"}]
            })

if __name__ == "__main__":
    unittest.main()
