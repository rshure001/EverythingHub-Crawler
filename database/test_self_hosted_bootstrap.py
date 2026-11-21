import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class SelfHostedBootstrapTests(unittest.TestCase):
    def test_compose_bootstraps_dedicated_schema_not_legacy_draft(self):
        compose = (HERE / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("image: postgres:17", compose)
        self.assertIn("./init/000_roles.sql:/docker-entrypoint-initdb.d/000_roles.sql:ro", compose)
        self.assertIn("./002_eh_quad_schema.sql:/docker-entrypoint-initdb.d/010_eh_quad_schema.sql:ro", compose)
        self.assertIn("./003_eh_quad_intelligence.sql:/docker-entrypoint-initdb.d/020_eh_quad_intelligence.sql:ro", compose)
        self.assertIn("./004_eh_quad_feedback_cursor.sql:/docker-entrypoint-initdb.d/030_eh_quad_feedback_cursor.sql:ro", compose)
        self.assertIn("./005_eh_quad_catalogs.sql:/docker-entrypoint-initdb.d/040_eh_quad_catalogs.sql:ro", compose)
        self.assertIn("./init/050_search_role.sh:/docker-entrypoint-initdb.d/050_search_role.sh:ro", compose)
        self.assertNotIn("001_everythinghub_core.sql", compose)
        self.assertIn('"127.0.0.1:55432:5432"', compose)
        self.assertIn('"127.0.0.1:58080:8080"', compose)
        self.assertIn("everythinghub_pgdata:/var/lib/postgresql/data", compose)

    def test_search_api_uses_separate_non_admin_database_login(self):
        compose = (HERE / "docker-compose.yml").read_text(encoding="utf-8")
        api_service = compose.split("everythinghub-search:", 1)[1]
        self.assertIn("postgresql://eh_search_api:", api_service)
        self.assertNotIn("postgresql://everythinghub:", api_service)
        self.assertIn("EH_DB_SCHEMA: eh_quad", api_service)

    def test_search_role_is_read_only_and_rls_scoped(self):
        roles = (HERE / "init" / "000_roles.sql").read_text(encoding="utf-8")
        setup = (HERE / "init" / "050_search_role.sh").read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE service_role NOLOGIN BYPASSRLS", roles)
        self.assertIn("CREATE ROLE eh_search_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT", roles)
        self.assertIn("WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT", setup)
        self.assertIn("eh_search_api_read_documents", setup)
        self.assertIn("robots_allowed IS TRUE", setup)
        self.assertIn("public_search_allowed", setup)
        self.assertNotIn("BYPASSRLS", setup)

    def test_environment_separates_admin_search_and_api_secrets(self):
        env = (HERE / ".env.example").read_text(encoding="utf-8")
        self.assertIn("EH_DB_PASSWORD=", env)
        self.assertIn("EH_SEARCH_DB_PASSWORD=", env)
        self.assertIn("EH_SEARCH_API_TOKEN=", env)
        self.assertIn("DATABASE_URL=postgresql://everythinghub:", env)

    def test_guide_warns_about_existing_volumes(self):
        guide = (HERE / "SELF_HOSTED.md").read_text(encoding="utf-8")
        self.assertIn("initialization scripts run only when the data directory is empty", guide)
        self.assertIn("docker compose down -v", guide)
        self.assertIn("managed Supabase database and this self-hosted database remain separate", guide)
