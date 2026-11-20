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
                "attributes": {"formats": ["CSV"]},
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


if __name__ == "__main__":
    unittest.main()
