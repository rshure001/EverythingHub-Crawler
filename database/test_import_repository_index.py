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

    def test_page_text_fields_remove_nul_before_postgres_insert(self):
        self.assertEqual(
            IMPORTER.page_text_fields({"title": "Ti\\x00tle", "description": "Desc\\x00ription", "text": "Bo\\x00dy"}),
            ("Title", "Description", "Body"),
        )

    def test_importer_skips_explicit_robots_denials(self):
        self.assertFalse(IMPORTER.is_robots_allowed({"robots_allowed": False}))
        self.assertTrue(IMPORTER.is_robots_allowed({}))
        self.assertTrue(IMPORTER.is_robots_allowed({"robots_allowed": True}))

    def test_robots_denied_urls_are_deduplicated_and_limited_to_public_http(self):
        pages = [
            {"url": "https://example.org/blocked", "robots_allowed": False},
            {"url": "https://example.org/blocked", "robots_allowed": False},
            {"url": "javascript:alert(1)", "robots_allowed": False},
            {"url": "file:///tmp/private", "robots_allowed": False},
            {"url": "https://example.org/allowed", "robots_allowed": True},
            None,
        ]
        self.assertEqual(
            IMPORTER.collect_robots_denied_urls(pages),
            ["https://example.org/blocked"],
        )

    def test_robots_denials_soft_disable_only_matching_eh_quad_rows(self):
        sql = IMPORTER.build_robots_exclusion_statement().lower()
        self.assertIn('update "eh_quad".eh_documents', sql)
        self.assertIn("set robots_allowed = false", sql)
        self.assertIn("where canonical_url = any(%s)", sql)
        self.assertIn("robots_allowed is distinct from false", sql)
        for destructive in ("delete ", "truncate ", "drop "):
            self.assertNotIn(destructive, sql)

    def test_public_http_url_rejects_credentials_and_malformed_authorities(self):
        rejected = (
            "https://user:secret@example.org/private",
            "https://@example.org/private",
            "https://example.org:invalid/path",
            "https://example.org:99999/path",
            "https://[2001:db8::1/path",
            "https://example.org/with space",
            " file:///tmp/private",
            "ftp://example.org/resource",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(IMPORTER.is_public_http_url(url))

        accepted = (
            "https://example.org/path",
            "http://example.org:8080/path",
            "https://[2001:db8::1]/path",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(IMPORTER.is_public_http_url(url))

    def test_upsert_restores_allowed_flag_when_policy_allows_again(self):
        sql = IMPORTER.build_upsert_statement().lower()
        self.assertIn("robots_allowed = excluded.robots_allowed", sql)
        self.assertIn("target.robots_allowed is distinct from excluded.robots_allowed", sql)

    def test_other_schemas_are_rejected(self):
        for schema in ("public", "auth", "storage", "dog_tag_day"):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    IMPORTER.target_schema(schema)

    def test_schema_selector_rejects_qualified_and_injected_names(self):
        for schema in (
            "public.eh_quad",
            "eh_quad ",
            "EH_QUAD",
            "eh_quad; drop schema public cascade",
            "eh_quad,public",
        ):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    IMPORTER.target_schema(schema)

    def test_upsert_is_schema_qualified(self):
        sql = IMPORTER.build_upsert_statement()
        self.assertIn('insert into "eh_quad".eh_documents', sql)
        self.assertNotIn('"public".', sql)
        self.assertNotIn("public.eh_documents", sql)

    def test_upsert_refreshes_lane_without_requiring_content_change(self):
        sql = IMPORTER.build_upsert_statement()
        self.assertIn("lane = excluded.lane", sql)
        self.assertIn("target.lane is distinct from excluded.lane", sql)

    def test_importer_classifies_unlabeled_pages(self):
        self.assertEqual(IMPORTER.page_lane({}, "https://www.nasa.gov/"), "science")
        self.assertEqual(IMPORTER.page_lane({}, "https://www.reuters.com/"), "news")
        self.assertEqual(IMPORTER.page_lane({}, "https://www.investopedia.com/"), "business")

    def test_importer_preserves_a_valid_existing_lane(self):
        self.assertEqual(
            IMPORTER.page_lane({"lane": "news"}, "https://www.nasa.gov/"),
            "news",
        )

    def test_migration_is_confined_to_eh_quad(self):
        sql = (HERE / "002_eh_quad_schema.sql").read_text(encoding="utf-8").lower()
        self.assertIn("create schema if not exists eh_quad", sql)
        self.assertIn("create table if not exists eh_quad.eh_documents", sql)
        self.assertIn("create table if not exists eh_quad.eh_crawl_queue", sql)
        self.assertIn("create or replace function eh_quad.eh_search_pages", sql)
        self.assertIn("and d.robots_allowed is true", sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("grant select on eh_quad.eh_documents to service_role", sql)
        self.assertNotIn("grant select on eh_quad.eh_documents to anon", sql)
        self.assertNotIn("public.", sql)
        self.assertNotIn("auth.", sql)
        self.assertNotIn("storage.", sql)

    def test_import_smoke_workflow_checks_exact_allowed_snapshot_count(self):
        workflow = (HERE.parent / ".github" / "workflows" / "import-smoke-test.yml").read_text(encoding="utf-8")
        self.assertIn('EH_DB_SCHEMA: eh_quad', workflow)
        self.assertIn('test "$count" = "$expected"', workflow)
        self.assertIn("collect_robots_denied_urls", workflow)
        self.assertIn("WHERE robots_allowed IS NOT TRUE", workflow)

    def test_search_api_defaults_to_and_enforces_eh_quad(self):
        source = (HERE / "search_api.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("EH_DB_SCHEMA", "eh_quad")', source)
        self.assertIn('if SCHEMA != "eh_quad":', source)
        self.assertNotIn('os.environ.get("EH_DB_SCHEMA", "public")', source)

    def test_python_search_and_count_both_exclude_robots_denied_rows(self):
        source = (HERE / "search_api.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("and robots_allowed is true"), 2)

    def test_edge_search_uses_only_eh_quad(self):
        source = (HERE.parent / "supabase" / "functions" / "everything-hub-web-search" / "index.ts").read_text(encoding="utf-8")
        self.assertIn('supabase.schema("eh_quad")', source)
        self.assertIn('db.rpc("eh_search_pages"', source)
        self.assertIn('.from("eh_documents")', source)
        self.assertNotIn('.from("eh_pages")', source)
        self.assertNotIn('supabase.rpc("eh_search_pages"', source)

    def test_legacy_search_edge_function_is_locked_to_eh_quad(self):
        source = (HERE.parent / "supabase" / "functions" / "everything-hub-search" / "index.ts").read_text(encoding="utf-8")
        self.assertIn('}).schema("eh_quad")', source)
        self.assertIn('db.rpc("eh_search_pages"', source)
        self.assertNotIn('createClient(supabaseUrl, serviceKey, { auth: { persistSession: false } });', source)
        self.assertNotIn('public.', source)


if __name__ == "__main__":
    unittest.main()
