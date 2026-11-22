import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import catalog_seed_expansion as expansion


class CatalogSeedExpansionTests(unittest.TestCase):
    def _write_gz(self, path, payload):
        path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8"), mtime=0))

    def test_extracts_unique_same_origin_article_links_only(self):
        html = """
        <html><head><title>Journal articles</title></head><body>
        <a href="/journals/demo/articles/one">Article One</a>
        <a href="/journals/demo/articles/one#top">Article One duplicate</a>
        <a href="https://evil.example/journals/demo/articles/two">External</a>
        <a href="/journals/demo/about">About</a>
        <a href="/journals/demo/articles/two">Article Two</a>
        </body></html>
        """
        rows = expansion.extract_child_records(
            html, "https://frontiersin.org/journals/demo/articles", "page-catalog:seed1"
        )
        self.assertEqual([x["title"] for x in rows], ["Article One", "Article Two"])
        self.assertEqual(rows[0]["rights_status"], "per_record_review_required")
        self.assertFalse(rows[0]["verified"])
        self.assertFalse(rows[0]["content_fetched"])

    def test_rejects_alternate_ports_and_credentials(self):
        parent = "https://frontiersin.org/journals/demo/articles"
        self.assertIsNone(expansion._canonical_child(parent, "https://frontiersin.org:8443/journals/demo/articles/one"))
        self.assertIsNone(expansion._canonical_child(parent, "https://user@frontiersin.org/journals/demo/articles/one"))
        self.assertIsNone(expansion._canonical_child("https://frontiersin.org:8443/journals/demo/articles", "/journals/demo/articles/one"))
        self.assertEqual(
            expansion._canonical_child(parent, "/journals/demo/articles/one"),
            "https://frontiersin.org/journals/demo/articles/one",
        )

    def test_policy_gate_rejects_unreviewed_or_unallowlisted_seed(self):
        with patch.object(expansion, "policy_for_host", return_value=None):
            self.assertFalse(expansion._eligible_seed({
                "catalog_seed_id": "page-catalog:x",
                "source_url": "https://unknown.example/articles",
            }))
        policy = {
            "discovery_allowed": True,
            "terms_status": "reviewed_per_dataset",
            "metadata_only": True,
            "allowed_metadata_fields": ["title", "article_url"],
            "prohibited_content": ["full_text"],
            "allowed_catalog_path_patterns": ["/journals/*/articles"],
        }
        with patch.object(expansion, "policy_for_host", return_value=policy):
            self.assertFalse(expansion._eligible_seed({
                "catalog_seed_id": "page-catalog:x",
                "source_url": "https://frontiersin.org/journals/demo/full",
            }))

    def test_batch_processes_at_most_ten_and_deduplicates_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds_path = root / "seeds.json.gz"
            state_path = root / "state.json.gz"
            seeds = [
                {"catalog_seed_id": f"page-catalog:{i}", "source_url": f"https://frontiersin.org/journals/demo{i}/articles"}
                for i in range(13)
            ]
            self._write_gz(seeds_path, {"pages": seeds})
            policy = {
                "discovery_allowed": True,
                "terms_status": "reviewed_per_dataset",
                "metadata_only": True,
                "allowed_metadata_fields": ["title", "article_url"],
                "prohibited_content": ["full_text"],
                "allowed_catalog_path_patterns": ["/journals/*/articles"],
                "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
            }
            html = b'<html><title>List</title><a href="/journals/demo0/articles/a">A</a></html>'
            with patch.object(expansion, "policy_for_host", return_value=policy), patch.object(
                expansion, "_purpose_path_allowed", return_value=True
            ), patch.object(
                expansion, "request_source_bytes", return_value=(html, 200, {"Content-Type": "text/html; charset=utf-8"}, policy)
            ) as request:
                first = expansion.run_batch(seeds_path, state_path, 10)
                self.assertEqual(first["last_batch_size"], 10)
                self.assertEqual(request.call_count, 10)
                second = expansion.run_batch(seeds_path, state_path, 10)
            self.assertEqual(second["last_batch_size"], 10)
            self.assertEqual(second["eligible_page_seeds"], 13)
            self.assertEqual(second["metrics"]["records_published"], 0)
            self.assertEqual(second["metrics"]["child_pages_fetched"], 0)

    def test_batch_limit_cannot_exceed_ten(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            expansion.run_batch(Path("missing.json.gz"), Path("state.json.gz"), 11)


if __name__ == "__main__":
    unittest.main()
