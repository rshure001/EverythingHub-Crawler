import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "import_repository_index.py"
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

    def test_migration_is_confined_to_eh_quad(self):
        sql = (HERE / "002_eh_quad_schema.sql").read_text(encoding="utf-8").lower()
        self.assertIn("create schema if not exists eh_quad", sql)
        self.assertIn("create table if not exists eh_quad.eh_documents", sql)
        self.assertIn("create table if not exists eh_quad.eh_crawl_queue", sql)
        self.assertIn("create or replace function eh_quad.eh_search_pages", sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("grant select on eh_quad.eh_documents to service_role", sql)
        self.assertNotIn("grant select on eh_quad.eh_documents to anon", sql)
        self.assertNotIn("public.", sql)
        self.assertNotIn("auth.", sql)
        self.assertNotIn("storage.", sql)

    def test_search_api_defaults_to_and_enforces_eh_quad(self):
        source = (HERE / "search_api.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("EH_DB_SCHEMA", "eh_quad")', source)
        self.assertIn('if SCHEMA != "eh_quad":', source)
        self.assertNotIn('os.environ.get("EH_DB_SCHEMA", "public")', source)

    def test_edge_search_uses_only_eh_quad(self):
        source = (HERE.parent / "supabase" / "functions" / "everything-hub-web-search" / "index.ts").read_text(encoding="utf-8")
        self.assertIn('supabase.schema("eh_quad")', source)
        self.assertIn('db.rpc("eh_search_pages"', source)
        self.assertIn('.from("eh_documents")', source)
        self.assertNotIn('.from("eh_pages")', source)
        self.assertNotIn('supabase.rpc("eh_search_pages"', source)


if __name__ == "__main__":
    unittest.main()
