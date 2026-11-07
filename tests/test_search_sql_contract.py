import pathlib
import re
import unittest

SQL = pathlib.Path("proposals/2026-11-07-search-narrow-topn.sql")


class SearchReplacementContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SQL.read_text(encoding="utf-8")

    def test_preserves_existing_rpc_signature_and_response_columns(self):
        self.assertIn("public.eh_search_pages(", self.sql)
        self.assertIn("search_query text", self.sql)
        self.assertIn("result_limit integer DEFAULT 10", self.sql)
        for column in ("title text", "url text", "description text",
                       "content_text text", "host text",
                       "fetched_at timestamptz", "relevance real"):
            self.assertIn(column, self.sql)

    def test_large_content_is_not_carried_through_ranked_sort(self):
        ranked = re.search(
            r"top_hits AS MATERIALIZED \((.*?)\n  \)\n  SELECT",
            self.sql,
            re.S,
        )
        self.assertIsNotNone(ranked)
        self.assertNotIn("content_text", ranked.group(1))
        self.assertIn("LIMIT greatest(1, least(coalesce(result_limit, 10), 20))",
                      ranked.group(1))
        self.assertIn("JOIN public.eh_pages AS p ON p.id = hits.id", self.sql)

    def test_candidate_set_is_bounded_and_uses_full_text_indexable_predicate(self):
        self.assertIn("p.search_vector @@ parsed.tsq", self.sql)
        self.assertIn("numnode(parsed.tsq) > 0", self.sql)
        self.assertIn("LIMIT greatest(1, least(coalesce(result_limit, 10), 20))",
                      self.sql)

    def test_exact_count_window_is_not_reintroduced(self):
        self.assertNotRegex(self.sql, r"count\s*\(\s*\*\s*\)\s*over")

    def test_security_definer_search_path_is_explicit(self):
        self.assertIn("SECURITY DEFINER", self.sql)
        self.assertIn("SET search_path TO pg_catalog, public", self.sql)


if __name__ == "__main__":
    unittest.main()
