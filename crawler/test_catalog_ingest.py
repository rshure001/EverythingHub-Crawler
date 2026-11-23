import gzip
import hashlib
import json
import unittest
from unittest.mock import patch
from catalog_ingest import adapt, build_public_catalog_indexes

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


    def test_explicit_restrictive_license_overrides_inconsistent_open_flag(self):
        restrictive_licenses = (
            {
                "id": "cc-by-nc-4.0",
                "title": "CC BY-NC 4.0",
                "url": "https://creativecommons.org/licenses/by-nc/4.0/",
                "is_open": True,
            },
            {
                "id": "cc-by-nd-4.0",
                "title": "CC BY-ND 4.0",
                "url": "https://creativecommons.org/licenses/by-nd/4.0/",
                "is_open": True,
            },
            {
                "rights": "Noncommercial use only; redistribution is not permitted",
                "is_open": True,
            },
        )
        for index, license_info in enumerate(restrictive_licenses, start=1):
            with self.subTest(license=license_info):
                result = adapt({
                    "updated_at": "2026-11-23T00:00:00Z",
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
                        "id": f"swiss-open-data:restricted-{index}",
                        "dataset_id": f"restricted-{index}",
                        "title": f"Restricted Dataset {index}",
                        "landing_url": f"https://opendata.swiss/dataset/restricted-{index}",
                        "license": license_info,
                        "resource_count": 0,
                    }],
                })
                record = result["records"][0]
                self.assertFalse(record["attributes"]["public_search_allowed"])
                self.assertEqual(record["attributes"]["rights_status"], "closed_license")
                self.assertEqual(result["metrics"]["restricted_records"], 1)

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

    def test_reviewed_library_archive_catalogs_can_be_staged_but_not_publicly_indexed(self):
        result = adapt({
            "updated_at": "2026-11-20T00:00:00Z",
            "catalogs": [{
                "id": "swiss-library-archive",
                "name": "Swiss Library Archive",
                "base_url": "https://opendata.swiss",
                "api_path": "/api/archive",
                "category": "science",
                "catalog_type": "library_archive",
                "coverage_status": "partial",
                "errors": [],
            }],
            "datasets": [{
                "catalog_id": "swiss-library-archive",
                "id": "swiss-library-archive:OL123W",
                "dataset_id": "OL123W",
                "title": "A Science Book",
                "landing_url": "https://opendata.swiss/works/OL123W",
                "record_type": "document",
                "canonical_entity_key": "swiss-library:work:OL123W",
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

    def test_restricted_source_catalogs_and_records_are_excluded_from_staging(self):
        restricted_policy = {
            "discovery_allowed": False,
            "terms_status": "restricted",
            "terms_url": "https://openlibrary.org/terms",
            "reviewed_at": "",
        }
        manifest = {
            "updated_at": "2026-11-23T00:00:00Z",
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
            }],
        }
        with patch("catalog_ingest.policy_for_host", return_value=restricted_policy):
            result = adapt(manifest)
        self.assertEqual(result["catalogs"], [])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["source_pages"], [])
        self.assertEqual(result["metrics"]["restricted_source_catalogs_excluded"], 1)
        self.assertEqual(result["metrics"]["restricted_source_records_excluded"], 1)
        self.assertEqual(result["excluded_sources"][0]["catalog_id"], "openlibrary-subject-science")

    def test_prior_restricted_snapshot_exclusions_are_reported_separately(self):
        manifest = {
            "updated_at": "2026-11-23T00:00:00Z",
            "catalogs": [{
                "id": "canada-open-data",
                "name": "Open Government Canada",
                "base_url": "https://open.canada.ca",
                "source_terms_url": "https://open.canada.ca/en/terms",
                "source_terms_status": "reviewed_per_dataset",
                "source_terms_reviewed_at": "2026-11-21",
            }],
            "datasets": [],
            "restricted_source_exclusions": [{
                "catalog_id": "openlibrary",
                "source_url": "https://openlibrary.org",
                "source_terms_status": "restricted",
                "records_excluded": 10500,
                "catalogs_excluded": 22,
                "reason": "source terms restrict bulk discovery",
            }],
        }
        result = adapt(manifest)
        self.assertEqual(result["metrics"]["restricted_source_records_excluded"], 10500)
        self.assertEqual(result["metrics"]["restricted_source_catalogs_excluded"], 22)
        self.assertEqual(result["excluded_sources"][0]["catalog_id"], "openlibrary")

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
        self.assertTrue(all(c["record_level_clearance"] == "eligible_per_record" for c in subcatalogs))
        self.assertTrue(all(c["metadata_only"] is False for c in subcatalogs))
        self.assertTrue(all(c["public_search_allowed"] is True for c in subcatalogs))
        index_manifest, _ = build_public_catalog_indexes(result)
        self.assertEqual(index_manifest["catalogs_with_public_records"], 3)
        self.assertEqual(index_manifest["unique_public_records"], 3)
        self.assertEqual(index_manifest["public_record_memberships"], 8)
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

    def test_same_canonical_reviewed_library_work_is_one_record_across_subject_catalogs(self):
        result = adapt({
            "updated_at": "2026-11-23T00:00:00Z",
            "catalogs": [
                {
                    "id": "swiss-library-archive-subject-science",
                    "name": "Swiss Library Archive — Science",
                    "base_url": "https://opendata.swiss",
                    "api_path": "/api/archive/science",
                    "category": "science",
                    "catalog_type": "library_archive",
                    "coverage_status": "partial",
                    "errors": [],
                },
                {
                    "id": "swiss-library-archive-subject-history",
                    "name": "Swiss Library Archive — History",
                    "base_url": "https://opendata.swiss",
                    "api_path": "/api/archive/history",
                    "category": "history",
                    "catalog_type": "library_archive",
                    "coverage_status": "partial",
                    "errors": [],
                },
            ],
            "datasets": [
                {
                    "catalog_id": "swiss-library-archive-subject-science",
                    "id": "swiss-library-archive-subject-science:OL123W",
                    "dataset_id": "OL123W",
                    "title": "A Science Book",
                    "landing_url": "https://opendata.swiss/works/OL123W",
                    "record_type": "document",
                    "canonical_entity_key": "swiss-library:work:OL123W",
                    "subject": "science",
                    "attributes": {"subjects": ["science"]},
                },
                {
                    "catalog_id": "swiss-library-archive-subject-history",
                    "id": "swiss-library-archive-subject-history:OL123W",
                    "dataset_id": "OL123W",
                    "title": "A Science Book",
                    "landing_url": "https://opendata.swiss/works/OL123W",
                    "record_type": "document",
                    "canonical_entity_key": "swiss-library:work:OL123W",
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
            next(c["catalog_id"] for c in result["catalogs"] if c["source_id"] == "swiss-library-archive-subject-science"),
            next(c["catalog_id"] for c in result["catalogs"] if c["source_id"] == "swiss-library-archive-subject-history"),
        })
        self.assertEqual(record["source_urls"], ["https://opendata.swiss/works/OL123W"])
        self.assertEqual(len(record["source_provenance"]), 2)
        self.assertFalse(record["attributes"]["public_search_allowed"])
        self.assertEqual(result["metrics"]["indexed_records"], 0)

    def test_cross_catalog_duplicate_with_any_restricted_source_is_fail_closed(self):
        reviewed_policy = {
            "discovery_allowed": True,
            "terms_status": "reviewed_per_dataset",
            "terms_url": "https://example.org/terms",
            "reviewed_at": "2026-11-21",
            "attribution_required": "per_dataset",
        }
        with patch("catalog_ingest.policy_for_host", return_value=reviewed_policy):
            result = adapt({
                "updated_at": "2026-11-23T00:00:00Z",
                "catalogs": [
                    {
                        "id": "public-source",
                        "name": "Public Source",
                        "base_url": "https://public.example",
                        "errors": [],
                    },
                    {
                        "id": "restricted-source",
                        "name": "Restricted Source",
                        "base_url": "https://restricted.example",
                        "errors": [],
                    },
                ],
                "datasets": [
                    {
                        "catalog_id": "public-source",
                        "id": "public-source:shared",
                        "dataset_id": "shared",
                        "canonical_entity_key": "dataset:shared-record",
                        "title": "Shared Dataset",
                        "landing_url": "https://public.example/dataset/shared",
                        "license": {
                            "id": "cc-by",
                            "url": "https://creativecommons.org/licenses/by/4.0/",
                            "is_open": True,
                        },
                    },
                    {
                        "catalog_id": "restricted-source",
                        "id": "restricted-source:shared",
                        "dataset_id": "shared",
                        "canonical_entity_key": "dataset:shared-record",
                        "title": "Shared Dataset",
                        "landing_url": "https://restricted.example/dataset/shared",
                        "license": {
                            "id": "cc-by-nc-4.0",
                            "url": "https://creativecommons.org/licenses/by-nc/4.0/",
                            "is_open": True,
                        },
                    },
                ],
            })

        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        self.assertFalse(record["attributes"]["public_search_allowed"])
        self.assertEqual(set(record["source_urls"]), {
            "https://public.example/dataset/shared",
            "https://restricted.example/dataset/shared",
        })
        self.assertEqual(
            {row["public_search_allowed"] for row in record["source_provenance"]},
            {True, False},
        )
        manifest, _ = build_public_catalog_indexes(result)
        self.assertEqual(manifest["unique_public_records"], 0)
        self.assertEqual(manifest["public_record_memberships"], 0)
        self.assertEqual(manifest["restricted_records_not_indexed"], 1)
        self.assertEqual(manifest["records_published"], 0)

    def test_rejects_untrusted_http_landing_url(self):
        with self.assertRaises(ValueError):
            adapt({
                "catalogs":[{"id":"canada-open-data","name":"Open Government Canada","base_url":"https://open.canada.ca"}],
                "datasets":[{"catalog_id":"canada-open-data","dataset_id":"x","title":"X","landing_url":"http://bad.example/x"}]
            })

    def test_rights_filtered_per_catalog_indexes_are_deterministic(self):
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
                    "organization": "Restricted Agency",
                    "tags": ["restricted-only"],
                    "license": {
                        "id": "ogl-canada",
                        "url": "https://open.canada.ca/en/open-government-licence-canada/",
                        "is_open": True,
                        "private": True,
                    },
                },
                {
                    "catalog_id": "canada-open-data",
                    "id": "canada-open-data:roads-4",
                    "dataset_id": "roads-4",
                    "title": "Road Network Four",
                    "landing_url": "https://open.canada.ca/data/en/dataset/roads-4",
                    "organization": "Restricted Agency",
                    "tags": ["restricted-only"],
                    "license": {
                        "id": "proprietary-license",
                        "title": "Proprietary License",
                        "url": "https://open.canada.ca/en/dataset/roads-4/license",
                        "is_open": False,
                        "private": False,
                    },
                },
                {
                    "catalog_id": "canada-open-data",
                    "id": "canada-open-data:roads-5",
                    "dataset_id": "roads-5",
                    "title": "Road Network Five",
                    "landing_url": "https://open.canada.ca/data/en/dataset/roads-5",
                    "organization": "Restricted Agency",
                    "tags": ["restricted-only"],
                    "license": {
                        "id": "unknown-license",
                        "title": "Unverified License",
                        "url": "https://open.canada.ca/en/dataset/roads-5/license",
                        "private": False,
                    },
                },
            ],
        })
        manifest, shards = build_public_catalog_indexes(result)
        root = next(c for c in result["catalogs"] if c["catalog_level"] == "root")
        subcatalogs = [c for c in result["catalogs"] if c["catalog_level"] == "subcatalog"]
        public_record = next(r for r in result["records"] if r["title"] == "Road Network One")
        second_public_record = next(r for r in result["records"] if r["title"] == "Road Network Two")
        private_record = next(r for r in result["records"] if r["title"] == "Road Network Three")
        closed_record = next(r for r in result["records"] if r["title"] == "Road Network Four")
        unknown_record = next(r for r in result["records"] if r["title"] == "Road Network Five")

        self.assertEqual(manifest["catalog_count"], len(result["catalogs"]))
        self.assertEqual(len(manifest["catalog_indexes"]), len(result["catalogs"]))
        self.assertEqual(len(subcatalogs), 1)
        self.assertEqual(manifest["catalogs_with_public_records"], 2)
        self.assertEqual(manifest["catalogs_without_public_records"], 0)
        self.assertEqual(manifest["unique_public_records"], 2)
        self.assertEqual(manifest["public_record_memberships"], 4)
        self.assertEqual(manifest["staged_record_catalog_memberships"], 7)
        self.assertEqual(manifest["restricted_records_not_indexed"], 3)
        self.assertEqual(manifest["eligible_records_without_public_index"], 0)
        self.assertEqual(manifest["records_published"], 0)
        self.assertFalse(manifest["production_indexed"])
        self.assertEqual(manifest["record_search_schema_version"], 1)
        self.assertEqual(manifest["unique_root_record_search_documents"], 2)
        self.assertEqual(manifest["root_record_search_document_occurrences"], 2)
        self.assertEqual(result["metrics"]["catalog_indexes_built"], len(result["catalogs"]))
        self.assertEqual(result["metrics"]["unique_public_records_indexed"], 2)
        self.assertEqual(result["metrics"]["public_record_catalog_memberships"], 4)
        self.assertEqual(result["metrics"]["restricted_records_excluded_from_public_indexes"], 3)
        self.assertEqual(result["metrics"]["production_eligible_records"], 2)
        self.assertEqual(result["metrics"]["restricted_records"], 3)
        self.assertEqual(result["metrics"]["records_published"], 0)
        self.assertEqual(result["metrics"]["indexed_records"], 0)

        rows = {}
        shard_entries = {row["file"]: row for row in manifest["shards"]}
        for file_name, compressed in shards.items():
            shard_entry = shard_entries[file_name]
            self.assertEqual(hashlib.sha256(compressed).hexdigest(), shard_entry["sha256"])
            self.assertEqual(len(compressed), shard_entry["bytes"])
            payload = json.loads(gzip.decompress(compressed))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["shard_id"], shard_entry["shard_id"])
            for row in payload["catalog_indexes"]:
                self.assertNotIn(row["catalog_id"], rows)
                rows[row["catalog_id"]] = row

        self.assertEqual(set(rows), {c["catalog_id"] for c in result["catalogs"]})
        public_ids = sorted([public_record["record_id"], second_public_record["record_id"]])
        self.assertEqual(rows[root["catalog_id"]]["record_ids"], public_ids)
        subcatalog = subcatalogs[0]
        self.assertEqual(rows[subcatalog["catalog_id"]]["record_ids"], public_ids)
        root_documents = rows[root["catalog_id"]]["record_search_documents"]
        self.assertEqual({document["record_id"] for document in root_documents}, set(public_ids))
        self.assertEqual(len(root_documents), 2)
        self.assertEqual(rows[root["catalog_id"]]["record_search_document_count"], 2)
        self.assertEqual(rows[subcatalog["catalog_id"]]["record_search_documents"], [])
        self.assertEqual(rows[subcatalog["catalog_id"]]["record_search_document_count"], 0)
        for document in root_documents:
            self.assertTrue(document["public_search_allowed"])
            self.assertTrue(document["source_url"].startswith("https://"))
            self.assertIn(root["catalog_id"], document["catalog_ids"])
            self.assertTrue(document["search_text"])
            self.assertNotIn(document["record_id"], {
                private_record["record_id"], closed_record["record_id"], unknown_record["record_id"]
            })
        self.assertEqual(subcatalog["record_count"], 2)
        self.assertEqual(subcatalog["record_level_clearance"], "eligible_per_record")
        self.assertFalse(subcatalog["metadata_only"])
        self.assertTrue(subcatalog["public_search_allowed"])
        self.assertNotIn("Restricted Agency", [c.get("subcategory_value") for c in subcatalogs])
        self.assertNotIn("restricted-only", [c.get("subcategory_value") for c in subcatalogs])
        indexed_record_ids = {record_id for row in rows.values() for record_id in row["record_ids"]}
        self.assertEqual(indexed_record_ids, set(public_ids))
        self.assertNotIn(private_record["record_id"], indexed_record_ids)
        self.assertNotIn(closed_record["record_id"], indexed_record_ids)
        self.assertNotIn(unknown_record["record_id"], indexed_record_ids)
        self.assertFalse(private_record["attributes"]["public_search_allowed"])
        self.assertFalse(closed_record["attributes"]["public_search_allowed"])
        self.assertEqual(closed_record["attributes"]["rights_status"], "closed_license")
        self.assertFalse(unknown_record["attributes"]["public_search_allowed"])
        self.assertEqual(unknown_record["attributes"]["rights_status"], "license_metadata_present")

        catalog_by_id = {c["catalog_id"]: c for c in result["catalogs"]}
        for entry in manifest["catalog_indexes"]:
            row = rows[entry["catalog_id"]]
            catalog = catalog_by_id[entry["catalog_id"]]
            self.assertEqual(row["index_sha256"], entry["index_sha256"])
            self.assertEqual(row["record_count"], entry["record_count"])
            self.assertEqual(catalog["public_index_shard"], entry["file"])
            self.assertEqual(catalog["public_index_sha256"], entry["index_sha256"])
            self.assertEqual(catalog["public_record_count"], entry["record_count"])
            self.assertEqual(entry["title"], catalog["title"])
            self.assertEqual(entry["source_url"], catalog["source_url"])
            self.assertEqual(entry["public_search_allowed"], catalog["public_search_allowed"])
            self.assertEqual(entry["record_search_document_count"], len(row["record_search_documents"]))

        second_manifest, second_shards = build_public_catalog_indexes(result)
        self.assertEqual(manifest, second_manifest)
        self.assertEqual(shards, second_shards)

if __name__ == "__main__":
    unittest.main()
