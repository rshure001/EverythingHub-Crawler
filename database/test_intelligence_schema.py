import re
import unittest
from pathlib import Path

SQL = (Path(__file__).with_name("003_eh_quad_intelligence.sql")
       .read_text(encoding="utf-8").lower())


class IntelligenceSchemaSafetyTests(unittest.TestCase):
    def test_all_objects_are_confined_to_eh_quad(self):
        self.assertIn("alter table eh_quad.eh_documents", SQL)
        self.assertIn("create table if not exists eh_quad.eh_source_registry", SQL)
        self.assertIn("create table if not exists eh_quad.eh_topics", SQL)
        self.assertIn("create table if not exists eh_quad.eh_document_topics", SQL)
        self.assertIn("create table if not exists eh_quad.eh_evidence_links", SQL)
        self.assertIn("create table if not exists eh_quad.eh_intelligence_feedback", SQL)
        self.assertIn("create table if not exists eh_quad.eh_rank_profiles", SQL)
        self.assertNotIn("public.", SQL)
        self.assertNotIn("auth.", SQL)
        self.assertNotIn("storage.objects", SQL)

    def test_migration_is_additive_and_idempotent(self):
        self.assertIn("add column if not exists", SQL)
        self.assertIn("create table if not exists", SQL)
        self.assertIn("on conflict (profile_name) do nothing", SQL)
        self.assertNotIn("drop table", SQL)
        self.assertNotIn("truncate ", SQL)
        self.assertNotIn("delete from", SQL)
        self.assertNotIn("drop schema", SQL)

    def test_security_rls_is_enabled_on_every_new_table(self):
        for table in (
            "eh_source_registry", "eh_topics", "eh_document_topics",
            "eh_evidence_links", "eh_intelligence_feedback", "eh_rank_profiles",
        ):
            self.assertIn(f"alter table eh_quad.{table} enable row level security", SQL)
        self.assertIn("to service_role", SQL)
        self.assertNotIn("to anon", SQL)
        self.assertNotIn("to authenticated", SQL)
        self.assertNotIn("to public", SQL)

    def test_feedback_is_append_only_and_identity_minimal(self):
        self.assertIn("grant select, insert on eh_quad.eh_intelligence_feedback", SQL)
        self.assertNotIn("update on eh_quad.eh_intelligence_feedback", SQL)
        self.assertNotIn("delete on eh_quad.eh_intelligence_feedback", SQL)
        self.assertNotIn("email_address", SQL)
        self.assertNotIn("user_id", SQL)

    def test_evidence_model_preserves_contradictions(self):
        self.assertIn("'corroborates','contradicts','updates','references'", SQL)
        self.assertIn("verification_status", SQL)
        self.assertIn("check (from_document_id <> to_document_id)", SQL)


if __name__ == "__main__":
    unittest.main()
