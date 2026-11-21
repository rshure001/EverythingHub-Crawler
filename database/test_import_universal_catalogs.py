import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_universal_catalogs as importer


class UniversalCatalogImporterTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "catalogs": [{
                "catalog_id": "catalog:one", "source_id": "one", "title": "One",
                "catalog_type": "open_data", "source_url": "https://example.org/api",
                "record_count": 1,
            }],
            "records": [{
                "record_id": "record:one", "catalog_id": "catalog:one",
                "record_type": "dataset", "title": "Dataset One",
                "source_key": "dataset-one", "source_url": "https://example.org/dataset/one",
                "attributes": {"formats": ["CSV"], "public_search_allowed": True},
            }],
        }

    def test_imports_only_eh_quad(self):
        self.assertEqual(importer.target_schema("eh_quad"), "eh_quad")
        with self.assertRaises(ValueError):
            importer.target_schema("public")
        self.assertIn("eh_quad.eh_catalogs", importer.catalog_upsert_sql())
        self.assertIn("eh_quad.eh_catalog_records", importer.record_upsert_sql())

    def test_builds_catalog_and_record_rows(self):
        catalogs = importer.build_catalog_rows(self.payload)
        records = importer.build_record_rows(self.payload)
        self.assertEqual(len(catalogs), 1)
        self.assertEqual(catalogs[0][0], "catalog:one")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0], "record:one")
        self.assertIn('"formats": ["CSV"]', records[0][10])

    def test_rejects_non_https_and_embedded_credentials(self):
        bad = {"catalogs": [{
            "catalog_id": "x", "source_id": "x", "title": "X",
            "catalog_type": "open_data", "source_url": "http://example.org/api",
        }], "records": []}
        with self.assertRaises(ValueError):
            importer.build_catalog_rows(bad)
        bad_record = {"catalogs": [], "records": [{
            "record_id": "r", "catalog_id": "c", "record_type": "dataset",
            "title": "R", "source_key": "r",
            "source_url": "https://user:pass@example.org/r",
        }]}
        with self.assertRaises(ValueError):
            importer.build_record_rows(bad_record)

    def test_rejects_missing_required_fields_and_non_object_attributes(self):
        with self.assertRaises(ValueError):
            importer.build_catalog_rows({"catalogs": [{"catalog_id": "x"}]})
        bad = {"records": [{
            "record_id": "r", "catalog_id": "c", "record_type": "dataset",
            "title": "R", "source_key": "r",
            "source_url": "https://example.org/r", "attributes": ["bad"],
        }]}
        with self.assertRaises(ValueError):
            importer.build_record_rows(bad)

    def test_skips_records_without_explicit_source_rights_approval(self):
        restricted = {
            "catalogs": [{
                "catalog_id": "catalog:restricted", "source_id": "restricted", "title": "Restricted",
                "catalog_type": "library_archive", "source_url": "https://example.org/api",
                "record_count": 1,
            }],
            "records": [{
                "record_id": "record:restricted", "catalog_id": "catalog:restricted",
                "record_type": "document", "title": "Restricted work",
                "source_key": "work-1", "source_url": "https://example.org/work/1",
                "attributes": {"public_search_allowed": False},
            }],
        }
        self.assertEqual(importer.build_record_rows(restricted), [])
        self.assertEqual(importer.build_catalog_rows(restricted)[0][11], 0)

    def test_preflight_accepts_well_formed_approved_payload(self):
        result = importer.validate_payload(self.payload)
        self.assertEqual(result, {
            "catalogs": 1, "records_total": 1,
            "records_public_search_approved": 1,
        })

    def test_preflight_rejects_duplicate_catalog_ids_and_source_ids(self):
        duplicate_id = {
            "catalogs": [
                self.payload["catalogs"][0],
                {**self.payload["catalogs"][0], "source_id": "two"},
            ],
            "records": [],
        }
        with self.assertRaisesRegex(ValueError, "duplicate catalog_id"):
            importer.validate_payload(duplicate_id)
        duplicate_source = {
            "catalogs": [
                self.payload["catalogs"][0],
                {**self.payload["catalogs"][0], "catalog_id": "catalog:two"},
            ],
            "records": [],
        }
        with self.assertRaisesRegex(ValueError, "duplicate source_id"):
            importer.validate_payload(duplicate_source)

    def test_preflight_rejects_duplicate_approved_record_keys(self):
        duplicate_id = {
            **self.payload,
            "records": [
                self.payload["records"][0],
                {**self.payload["records"][0], "source_key": "dataset-two"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "duplicate approved record_id"):
            importer.validate_payload(duplicate_id)
        duplicate_source_key = {
            **self.payload,
            "records": [
                self.payload["records"][0],
                {**self.payload["records"][0], "record_id": "record:two"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "duplicate approved catalog/source_key"):
            importer.validate_payload(duplicate_source_key)

    def test_preflight_rejects_approved_records_with_unknown_catalog(self):
        bad = {
            "catalogs": self.payload["catalogs"],
            "records": [{**self.payload["records"][0], "catalog_id": "catalog:missing"}],
        }
        with self.assertRaisesRegex(ValueError, "unknown catalog_id"):
            importer.validate_payload(bad)

    def test_preflight_ignores_duplicate_keys_for_restricted_records(self):
        restricted = {
            "catalogs": self.payload["catalogs"],
            "records": [
                {**self.payload["records"][0], "attributes": {"public_search_allowed": False}},
                {**self.payload["records"][0], "attributes": {"public_search_allowed": False}},
            ],
        }
        result = importer.validate_payload(restricted)
        self.assertEqual(result["records_public_search_approved"], 0)


if __name__ == "__main__":
    unittest.main()
