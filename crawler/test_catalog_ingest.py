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
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["metrics"]["catalogs_discovered"],1)
        self.assertEqual(result["metrics"]["root_catalogs_discovered"],1)
        self.assertEqual(result["metrics"]["subcatalogs_discovered"],0)
        self.assertEqual(result["metrics"]["record_catalog_memberships"],1)
        self.assertEqual(result["metrics"]["source_page_associations"],1)
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


    def test_private_dataset_never_becomes_public_searchable(self):
        result = adapt({
            "updated_at": "2026-11-21T00:00:00Z",
            "catalogs": [{
                "id": "swiss-open-data", "name": "opendata.swiss",
                "base_url": "https://opendata.swiss",
                "source_terms_url": "https://opendata.swiss/en/terms-of-use",
                "source_terms_status": "reviewed_per_dataset",
                "source_terms_reviewed_at": "2026-11-21",
                "errors": [],
            }],
            "datasets": [{
                "catalog_id": "swiss-open-data", "id": "swiss-open-data:private",
                "dataset_id": "private", "title": "Private Dataset",
                "landing_url": "https://opendata.swiss/dataset/private",
                "license": {"rights": "Open use", "is_open": True, "private": True},
                "resource_count": 0,
            }],
        })
        self.assertFalse(result["records"][0]["attributes"]["public_search_allowed"])
        self.assertEqual(result["records"][0]["attributes"]["rights_status"], "private_dataset")
        self.assertFalse(result["source_pages"][0]["policy_allowed"])

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

    def test_ckan_metadata_creates_bounded_organization_and_tag_subcatalogs(self):
        result = adapt({
            "updated_at": "2026-11-23T00:00:00Z",
            "catalogs": [{
                "id": "canada-open-data",
                "name": "Open Government Canada",
                "base_url": "https://open.canada.ca",
                "api_path": "/data/en/api/3/action/package_search",
                "source_url": "https://open.canada.ca/data/en/api/3/action/package_search",
                "category": "government",
                "errors": [],
            }],
            "datasets": [
                {
                    "catalog_id": "canada-open-data",
                    "id": "canada-open-data:roads-1",
                    "dataset_id": "roads-1",
                    "title": "Road Network One",
                    "landing_url": "https://open.canada.ca/data/en/dataset/roads-1",
                    "organization": "Transport Canada",
                    "tags": ["roads", "transport"],
                    "license": {
                        "id": "ogl-canada",
                        "title": "Open Government Licence - Canada",
                        "url": "https://open.canada.ca/en/open-government-licence-canada/",
                        "is_open": True,
                        "private": False,
                    },
                },
                {
                    "catalog_id": "canada-open-data",
                    "id": "canada-open-data:roads-2",
                    "dataset_id": "roads-2",
                    "title": "Road Network Two",
                    "landing_url": "https://open.canada.ca/data/en/dataset/roads-2",
                    "organization": "Transport Canada",
                    "tags": ["roads", "transport"],
                    "license": {
                        "id": "ogl-canada",
                        "url": "https://open.canada.ca/en/open-government-licence-canada/",
                        "is_open": True,
                        "private": False,
                    },
                },
                {
                    "catalog_id": "canada-open-data",
                    "id": "canada-open-data:roads-3",
                    "dataset_id": "roads-3",
                    "title": "Road Network Three",
                    "landing_url": "https://open.canada.ca/data/en/dataset/roads-3",
                    "organization": "Health Canada",
                    "tags": ["roads", "health"],
                    "license": {
                        "id": "ogl-canada",
                        "url": "https://open.canada.ca/en/open-government-licence-canada/",
                        "is_open": True,
                        "private": False,
                    },
                },
            ],
        })
        root = next(c for c in result["catalogs"] if c["catalog_level"] == "root")
        subcatalogs = [c for c in result["catalogs"] if c["catalog_level"] == "subcatalog"]
        self.assertEqual(result["metrics"]["catalogs_discovered"], 3)
        self.assertEqual(result["metrics"]["root_catalogs_discovered"], 1)
        self.assertEqual(result["metrics"]["subcatalogs_discovered"], 2)
        self.assertEqual(result["metrics"]["catalog_records"], 3)
        self.assertEqual(result["metrics"]["record_catalog_memberships"], 8)
        self.assertEqual(result["metrics"]["catalogs_with_records"], 3)
        self.assertEqual(result["metrics"]["catalogs_without_records"], 0)
        self.assertEqual(root["record_count"], 3)
        self.assertEqual(sorted(c["record_count"] for c in subcatalogs), [2, 3])
        self.assertEqual(len(subcatalogs), 2)
        self.assertEqual({c["subcategory_kind"] for c in subcatalogs}, {"organization", "tag"})
        self.assertTrue(all(c["parent_catalog_id"] == root["catalog_id"] for c in subcatalogs))
        self.assertTrue(all(c["record_level_clearance"] == "not_reviewed" for c in subcatalogs))
        self.assertTrue(all(c["public_search_allowed"] is False for c in subcatalogs))
        by_title = {r["title"]: r for r in result["records"]}
        transport = next(c for c in subcatalogs if c["subcategory_kind"] == "organization")
        roads = next(c for c in subcatalogs if c["subcategory_kind"] == "tag")
        self.assertIn(transport["catalog_id"], by_title["Road Network One"]["catalog_ids"])
        self.assertIn(transport["catalog_id"], by_title["Road Network Two"]["catalog_ids"])
        self.assertNotIn(transport["catalog_id"], by_title["Road Network Three"]["catalog_ids"])
        self.assertTrue(all(roads["catalog_id"] in r["catalog_ids"] for r in by_title.values()))
        self.assertEqual(result["metrics"]["production_eligible_records"], 3)
        self.assertEqual(result["metrics"]["indexed_records"], 0)
        self.assertEqual(result["metrics"]["records_published"], 0)
        self.assertFalse(result["production_indexed"])

    def test_same_canonical_openlibrary_work_is_one_record_across_subject_catalogs(self):
        result = adapt({
            "updated_at": "2026-11-23T00:00:00Z",
            "catalogs": [
                {
                    "id": "openlibrary-subject-science",
                    "name": "Open Library — Science",
                    "base_url": "https://openlibrary.org",
                    "api_path": "/subjects/science.json",
                    "category": "science",
                    "catalog_type": "library_archive",
                    "coverage_status": "partial",
                    "errors": [],
                },
                {
                    "id": "openlibrary-subject-history",
                    "name": "Open Library — History",
                    "base_url": "https://openlibrary.org",
                    "api_path": "/subjects/history.json",
                    "category": "history",
                    "catalog_type": "library_archive",
                    "coverage_status": "partial",
                    "errors": [],
                },
            ],
            "datasets": [
                {
                    "catalog_id": "openlibrary-subject-science",
                    "id": "openlibrary-subject-science:OL123W",
                    "dataset_id": "OL123W",
                    "title": "A Science Book",
                    "landing_url": "https://openlibrary.org/works/OL123W",
                    "record_type": "document",
                    "canonical_entity_key": "openlibrary:work:OL123W",
                    "subject": "science",
                    "attributes": {"subjects": ["science"]},
                },
                {
                    "catalog_id": "openlibrary-subject-history",
                    "id": "openlibrary-subject-history:OL123W",
                    "dataset_id": "OL123W",
                    "title": "A Science Book",
                    "landing_url": "https://openlibrary.org/works/OL123W",
                    "record_type": "document",
                    "canonical_entity_key": "openlibrary:work:OL123W",
                    "subject": "history",
                    "attributes": {"subjects": ["history"]},
                },
            ],
        })
        self.assertEqual(result["metrics"]["catalogs_discovered"], 2)
        self.assertEqual(result["metrics"]["root_catalogs_discovered"], 2)
        self.assertEqual(result["metrics"]["subcatalogs_discovered"], 0)
        self.assertEqual(result["metrics"]["catalog_records"], 1)
        self.assertEqual(result["metrics"]["unique_entities"], 1)
        self.assertEqual(result["metrics"]["record_catalog_memberships"], 2)
        self.assertEqual(result["metrics"]["source_page_references"], 1)
        self.assertEqual(result["metrics"]["source_page_associations"], 2)
        self.assertEqual(result["metrics"]["catalogs_with_records"], 2)
        self.assertEqual(result["metrics"]["catalogs_without_records"], 0)
        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        self.assertEqual(set(record["catalog_ids"]), {
            next(c["catalog_id"] for c in result["catalogs"] if c["source_id"] == "openlibrary-subject-science"),
            next(c["catalog_id"] for c in result["catalogs"] if c["source_id"] == "openlibrary-subject-history"),
        })
        self.assertEqual(record["source_urls"], ["https://openlibrary.org/works/OL123W"])
        self.assertEqual(len(record["source_provenance"]), 2)
        self.assertFalse(record["attributes"]["public_search_allowed"])
        self.assertEqual(result["metrics"]["indexed_records"], 0)

    def test_rejects_untrusted_http_landing_url(self):
        with self.assertRaises(ValueError):
            adapt({
                "catalogs":[{"id":"test","name":"Test","base_url":"https://data.example.org"}],
                "datasets":[{"catalog_id":"test","dataset_id":"x","title":"X","landing_url":"http://bad.example/x"}]
            })

if __name__ == "__main__":
    unittest.main()
