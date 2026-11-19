from pathlib import Path
import re
import unittest


SQL_PATH = Path(__file__).resolve().parents[1] / "database" / "eh_quad_recovery_preflight.sql"


def uncomment(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", sql)


class EhQuadRecoveryPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = uncomment(SQL_PATH.read_text(encoding="utf-8"))

    def test_all_statements_are_select_only(self):
        statements = [part.strip() for part in self.sql.split(";") if part.strip()]
        self.assertGreaterEqual(len(statements), 5)
        for statement in statements:
            self.assertRegex(statement, r"(?is)^select\b")
        self.assertNotRegex(
            self.sql,
            r"(?i)\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|do|copy|call|vacuum|analyze|refresh)\b",
        )

    def test_catalog_reads_are_system_catalog_only(self):
        relations = re.findall(
            r"(?i)\b(?:from|join)\s+([a-z_][a-z0-9_.]*)",
            self.sql,
        )
        self.assertTrue(relations)
        self.assertTrue(
            all(name.startswith("pg_catalog.") for name in relations),
            msg=f"Unexpected relation outside pg_catalog: {relations}",
        )

    def test_everything_hub_objects_are_confined_to_eh_quad(self):
        for name in (
            "eh_quad.eh_documents",
            "eh_quad.eh_crawl_queue",
            "eh_quad.eh_host_leases",
            "eh_quad.eh_crawl_runs",
            "eh_quad.eh_media",
            "eh_quad.eh_lane_state",
            "eh_quad.eh_search_pages",
        ):
            self.assertIn(name, self.sql)
        self.assertNotRegex(self.sql, r"(?i)\b(?:public|auth|storage|eh_search)\s*\.")

    def test_preflight_checks_rls_privileges_and_policies(self):
        for marker in (
            "relrowsecurity",
            "relforcerowsecurity",
            "service_role",
            "anon_documents_select",
            "authenticated_documents_select",
            "pg_policies",
        ):
            self.assertIn(marker, self.sql)


if __name__ == "__main__":
    unittest.main()
