import unittest
from catalog_ingest import adapt

class UniversalCatalogIngestTests(unittest.TestCase):
    def test_ckan_datasets_become_catalog_records_not_pages(self):
        result = adapt({
            "updated_at": "2026-11-20T00:00:00Z",
            "catalogs": [{"id":"test","name":"Test Data","base_url":"https://opendata.swiss","source_terms_url":"https://opendata.swiss/en/terms-of-use","source_terms_status":"reviewed_per_dataset","source_terms_reviewed_at":"2026-11-21","attribution_required":"per_dataset","errors":[]}],
            "datasets": [{
                "catalog_id":"test","id":"test:roads","dataset_id":"roads","title":"Road Network",
                "description":"Roads and routes","landing_url":"https://opendata.swiss/dataset/roads",
                "organization":"Transport","tags":["roads","maps"],
                "resources":[{"name":"GeoJSON","format":"GEOJSON","url":"https://opendata.swiss/roads.geojson"}],
                "license":{"id":"cc-by","url":"https://creativecommons.org/licenses/by/4.0/","title":"CC BY"},
                "source_terms_url":"https://opendata.swiss/en/terms-of-use","source_terms_status":"reviewed_per_dataset",
                "resource_count":1,"metadata_modified":"2026-11-19"
            }]
        })
        self.assertEqual(result["metrics"]["catalogs_discovered"],1)
        self.assertEqual(result["metrics"]["catalog_records"],1)
        self.assertEqual(result["metrics"]["source_page_references"],1)
        self.assertEqual(result["metrics"]["source_pages_fetched"],0)
        self.assertEqual(result["metrics"]["indexed_records"],0)
        self.assertFalse(result["production_indexed"])
        self.assertEqual(result["records"][0]["record_type"],"dataset")
        self.assertEqual(result["records"][0]["attributes"]["resource_count"],1)
        self.assertTrue(result["records"][0]["attributes"]["public_search_allowed"])
        self.assertTrue(result["source_pages"][0]["policy_allowed"])
        self.assertEqual(result["metrics"]["production_eligible_records"],1)

    def test_dataset_rights_metadata_is_preserved_without_claiming_commercial_clearance(self):
        result = adapt({
            "updated_at": "2026-11-21T00:00:00Z",
            "catalogs": [{
                "id": "swiss-open-data",
                "name": "opendata.swiss",
                "base_url": "https://opendata.swiss",
                "source_terms_url": "https://opendata.swiss/en/terms-of-use",
                "source_terms_status": "reviewed_per_dataset",
                "source_terms_reviewed_at": "2026-11-21",
                "attribution_required": "per_dataset",
                "errors": [],
            }],
            "datasets": [{
                "catalog_id": "swiss-open-data",
                "id": "swiss-open-data:transport",
                "dataset_id": "transport",
                "title": "Transport Statistics",
                "landing_url": "https://opendata.swiss/dataset/transport",
                "license": {"rights": "Open use. Must provide the source", "is_open": False},
                "resources": [{
                    "name": "CSV",
                    "format": "CSV",
                    "url": "https://opendata.swiss/resources/transport.csv",
                    "rights": "Open use. Must provide the source",
                }],
                "resource_count": 1,
            }],
        })
        record = result["records"][0]
        self.assertTrue(record["attributes"]["public_search_allowed"])
        self.assertEqual(record["attributes"]["rights_status"], "dataset_rights_metadata_present")
        self.assertEqual(record["attributes"]["license"]["rights"], "Open use. Must provide the source")
        self.assertEqual(record["attributes"]["resources"][0]["rights"], "Open use. Must provide the source")
        self.assertIsNot(record["attributes"].get("commercial_eligible"), True)

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
        self.assertFalse(result["records"][0]["attributes"]["public_search_allowed"])
        self.assertFalse(result["source_pages"][0]["policy_allowed"])
        self.assertEqual(result["metrics"]["restricted_records"], 1)
        self.assertEqual(result["catalogs"][0]["catalog_type"], "library_archive")

    def test_rejects_untrusted_http_landing_url(self):
        with self.assertRaises(ValueError):
            adapt({
                "catalogs":[{"id":"test","name":"Test","base_url":"https://data.example.org"}],
                "datasets":[{"catalog_id":"test","dataset_id":"x","title":"X","landing_url":"http://bad.example/x"}]
            })

if __name__ == "__main__":
    unittest.main()
