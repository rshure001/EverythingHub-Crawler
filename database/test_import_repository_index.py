import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("import_repository_index.py")
SPEC = importlib.util.spec_from_file_location("eh_importer", MODULE_PATH)
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


class SchemaIsolationTests(unittest.TestCase):
    def test_default_schema_is_eh_quad(self):
        self.assertEqual(IMPORTER.target_schema("eh_quad"), "eh_quad")

    def test_other_schemas_are_rejected(self):
        for schema in ("public", "auth", "storage", "dog_tag_day"):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    IMPORTER.target_schema(schema)

    def test_upsert_is_schema_qualified(self):
        sql = IMPORTER.build_upsert_statement()
        self.assertIn('insert into "eh_quad".eh_documents', sql)
        self.assertNotIn('"public".', sql)
        self.assertNotIn("public.eh_documents", sql)


if __name__ == "__main__":
    unittest.main()
