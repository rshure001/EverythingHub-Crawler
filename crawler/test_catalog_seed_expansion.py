import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import catalog_seed_expansion as expansion


class CatalogSeedExpansionTests(unittest.TestCase):
    def _write_payload(self, path, payload):
        raw = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"
        if path.suffix == ".gz":
            path.write_bytes(gzip.compress(raw, mtime=0))
        else:
            path.write_bytes(raw)
        return raw

    def _policy(self):
        return {
            "discovery_allowed": True,
            "terms_status": "reviewed_per_dataset",
            "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
            "metadata_only": True,
            "allowed_metadata_fields": ["title", "article_url"],
            "prohibited_content": ["full_text", "media", "bulk_assets"],
            "allowed_catalog_path_patterns": ["/journals/*/articles"],
            "attribution_required": "per_dataset",
            "rights_scope": "metadata_only",
        }

    def _queue_row(self, index, catalog_id=None):
        return {
            "source_url": f"https://frontiersin.org/journals/demo{index}/articles",
            "catalog_ids": [catalog_id or f"catalog:demo:{index}"],
            "review_status": "policy_preapproved_robots_recheck_required",
            "source_access_mode": "catalog_landing_page",
            "policy_preapproved": True,
            "metadata_only": True,
            "robots_recheck_required": True,
            "fetch_authorized": False,
            "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
            "terms_status": "reviewed_per_dataset",
            "allowed_metadata_fields": ["title", "article_url"],
            "prohibited_content": ["full_text", "media", "bulk_assets"],
            "attribution_required": "per_dataset",
            "rights_scope": "metadata_only",
        }

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
            html,
            "https://frontiersin.org/journals/demo",
            ["catalog:demo:1", "catalog:demo:2"],
            {"terms_url": "https://example.org/terms", "terms_status": "reviewed_per_dataset"},
        )
        self.assertEqual([x["title"] for x in rows], ["Article One", "Article Two"])
        self.assertEqual(rows[0]["catalog_ids"], ["catalog:demo:1", "catalog:demo:2"])
        self.assertEqual(rows[0]["rights_status"], "per_record_review_required")
        self.assertFalse(rows[0]["verified"])
        self.assertFalse(rows[0]["content_fetched"])
        self.assertEqual(rows[0]["source_provenance"][0]["terms_status"], "reviewed_per_dataset")

    def test_canonical_fetch_url_uses_only_independently_reviewed_host(self):
        apex = self._policy()
        apex["canonical_fetch_host"] = "www.frontiersin.org"
        www = dict(apex)
        with patch.object(expansion, "policy_for_host", side_effect=lambda host: apex if host == "frontiersin.org" else www if host == "www.frontiersin.org" else None), patch.object(
            expansion, "_purpose_path_allowed", return_value=True
        ):
            self.assertEqual(
                expansion._canonical_fetch_url("https://frontiersin.org/journals/demo/articles", apex),
                "https://www.frontiersin.org/journals/demo/articles",
            )
            self.assertEqual(
                expansion._canonical_fetch_url("https://www.frontiersin.org/journals/demo/articles", www),
                "https://www.frontiersin.org/journals/demo/articles",
            )
            with self.assertRaises(expansion.SourcePolicyError):
                expansion._canonical_fetch_url("https://frontiersin.org:8443/journals/demo/articles", apex)
            with self.assertRaises(expansion.SourcePolicyError):
                expansion._canonical_fetch_url("https://frontiersin.org/journals/demo/articles", {**apex, "canonical_fetch_host": "unreviewed.example"})

    def test_rejects_alternate_ports_and_credentials(self):
        parent = "https://frontiersin.org/journals/demo/articles"
        self.assertIsNone(expansion._canonical_child(parent, "https://frontiersin.org:8443/journals/demo/articles/one"))
        self.assertIsNone(expansion._canonical_child(parent, "https://user@frontiersin.org/journals/demo/articles/one"))
        self.assertIsNone(expansion._canonical_child("https://frontiersin.org:8443/journals/demo/articles", "/journals/demo/articles/one"))
        self.assertEqual(
            expansion._canonical_child(parent, "/journals/demo/articles/one"),
            "https://frontiersin.org/journals/demo/articles/one",
        )

    def test_policy_gate_requires_a_preapproved_catalog_definition_url(self):
        valid = self._queue_row(0)
        with patch.object(expansion, "policy_for_host", return_value=self._policy()), patch.object(
            expansion, "_purpose_path_allowed", return_value=True
        ):
            self.assertTrue(expansion._eligible_seed(valid))
            self.assertFalse(
                expansion._eligible_seed(
                    {
                        "catalog_seed_id": "page-catalog:one-page-placeholder",
                        "source_url": valid["source_url"],
                        "catalog_type": "general",
                        "catalog_status": "pending_catalog_classification",
                    }
                )
            )
            unreviewed = dict(valid, review_status="policy_review_required")
            self.assertFalse(expansion._eligible_seed(unreviewed))
            api_only = dict(valid, source_access_mode="api")
            self.assertFalse(expansion._eligible_seed(api_only))
            authorized_flag = dict(valid, fetch_authorized=True)
            self.assertFalse(expansion._eligible_seed(authorized_flag))

    def test_stale_review_queue_never_makes_network_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index_path = root / "manifest.json"
            queue_path = root / "queue.json"
            state_path = root / "state.json.gz"
            self._write_payload(index_path, {"total_pages": 5, "updated_at": 1795429785})
            queue = {
                "status": "source_review_backlog_not_a_fetch_queue",
                "source_index_pages": 4,
                "source_index_updated_at": "2026-11-22T00:00:00Z",
                "source_index_sha256": "0" * 64,
                "candidate_urls": [self._queue_row(0)],
            }
            self._write_payload(queue_path, queue)
            with patch.object(expansion, "request_source_bytes") as request:
                result = expansion.run_batch(queue_path, state_path, 10, index_path)
            self.assertEqual(result["status"], "waiting_for_fresh_source_review_queue")
            self.assertFalse(result["queue_matches_current_index"])
            self.assertFalse(result["queue_snapshot_compatible_with_current_index"])
            self.assertEqual(result["eligible_catalog_landing_pages"], 0)
            self.assertFalse(state_path.exists())
            request.assert_not_called()

            # An existing resumable ledger must also remain byte-for-byte intact.
            self._write_payload(state_path, {"records": [], "source_outcomes": {}})
            previous_state = state_path.read_bytes()
            with patch.object(expansion, "request_source_bytes") as request:
                result = expansion.run_batch(queue_path, state_path, 10, index_path)
            self.assertEqual(result["status"], "waiting_for_fresh_source_review_queue")
            self.assertEqual(state_path.read_bytes(), previous_state)
            request.assert_not_called()

    def test_same_page_count_with_changed_index_hash_is_not_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.json"
            state_path = root / "state.json.gz"
            index_path = root / "manifest.json"
            self._write_payload(index_path, {"total_pages": 5, "updated_at": 1795429785})
            queue = {
                "status": "source_review_backlog_not_a_fetch_queue",
                "source_index_pages": 5,
                "source_index_updated_at": "2026-11-23T10:20:00Z",
                "source_index_sha256": "0" * 64,
                "candidate_urls": [self._queue_row(0)],
            }
            self._write_payload(queue_path, queue)
            with patch.object(expansion, "request_source_bytes") as request:
                result = expansion.run_batch(queue_path, state_path, 10, index_path)
            self.assertEqual(result["status"], "waiting_for_fresh_source_review_queue")
            self.assertFalse(result["queue_snapshot_compatible_with_current_index"])
            self.assertEqual(result["eligible_catalog_landing_pages"], 0)
            self.assertFalse(state_path.exists())
            request.assert_not_called()

    def test_recent_append_only_queue_remains_compatible_after_crawler_adds_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.json"
            state_path = root / "state.json.gz"
            index_path = root / "manifest.json"
            self._write_payload(index_path, {"total_pages": 5, "updated_at": 1795429785})
            queue = {
                "status": "source_review_backlog_not_a_fetch_queue",
                "source_index_pages": 4,
                "source_index_updated_at": "2026-11-23T10:20:00Z",
                "source_index_sha256": "0" * 64,
                "candidate_urls": [self._queue_row(0)],
            }
            self._write_payload(queue_path, queue)
            policy = self._policy()

            with patch.object(expansion, "policy_for_host", return_value=policy), patch.object(
                expansion, "_purpose_path_allowed", return_value=True
            ), patch.object(
                expansion,
                "request_source_bytes",
                return_value=(
                    b'<html><a href="/journals/demo0/articles/one">Article One</a></html>',
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    policy,
                ),
            ) as request:
                self.assertTrue(expansion._queue_snapshot_compatible_with_current_index(queue, index_path))
                self.assertFalse(expansion._queue_matches_current_index(queue, index_path))
                result = expansion.run_batch(queue_path, state_path, 10, index_path)

            self.assertFalse(result["queue_matches_current_index"])
            self.assertTrue(result["queue_snapshot_compatible_with_current_index"])
            self.assertEqual(result["queue_source_index_page_lag"], 1)
            self.assertEqual(result["last_batch_processed"], 1)
            self.assertEqual(result["last_batch_new_unique_child_records"], 1)
            request.assert_called_once()

    def test_batch_processes_at_most_ten_and_deduplicates_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.json"
            state_path = root / "state.json.gz"
            index_path = root / "manifest.json"
            index_raw = self._write_payload(index_path, {"total_pages": 13, "updated_at": 1795429785})
            queue = {
                "status": "source_review_backlog_not_a_fetch_queue",
                "source_index_pages": 13,
                "source_index_updated_at": "2026-11-23T10:29:45Z",
                "source_index_sha256": hashlib.sha256(index_raw).hexdigest(),
                "candidate_urls": [self._queue_row(i) for i in range(13)],
            }
            self._write_payload(queue_path, queue)
            policy = self._policy()

            def fake_request(url, **kwargs):
                path = urlsplit(url).path
                label = path.split("/")[2]
                html = f'<html><a href="{path}/article">Article {label}</a></html>'.encode()
                return html, 200, {"Content-Type": "text/html; charset=utf-8"}, policy

            with patch.object(expansion, "policy_for_host", return_value=policy), patch.object(
                expansion, "_purpose_path_allowed", return_value=True
            ), patch.object(expansion, "request_source_bytes", side_effect=fake_request) as request:
                first = expansion.run_batch(queue_path, state_path, 10, index_path)
                self.assertEqual(first["last_batch_size"], 10)
                self.assertEqual(request.call_count, 10)
                second = expansion.run_batch(queue_path, state_path, 10, index_path)

            self.assertEqual(second["last_batch_size"], 3)
            self.assertEqual(second["eligible_catalog_landing_pages"], 13)
            self.assertEqual(second["metrics"]["unique_child_records_staged"], 13)
            self.assertEqual(second["metrics"]["records_published"], 0)
            self.assertEqual(second["metrics"]["child_pages_fetched"], 0)
            self.assertEqual(request.call_count, 13)
            third = expansion.run_batch(queue_path, state_path, 10, index_path)
            self.assertEqual(third["last_batch_size"], 0)
            self.assertEqual(request.call_count, 13)

    def test_recent_failed_sources_are_cooldown_skipped_and_cursor_advances(self):
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.json"
            state_path = root / "state.json.gz"
            index_path = root / "manifest.json"
            index_raw = self._write_payload(index_path, {"total_pages": 3, "updated_at": 1795429785})
            seeds = [self._queue_row(i) for i in range(3)]
            queue = {
                "status": "source_review_backlog_not_a_fetch_queue",
                "source_index_pages": 3,
                "source_index_updated_at": "2026-11-23T10:29:45Z",
                "source_index_sha256": hashlib.sha256(index_raw).hexdigest(),
                "candidate_urls": seeds,
            }
            self._write_payload(queue_path, queue)
            state = {
                "next_source_offset": 0,
                "records": [],
                "source_outcomes": {
                    expansion._source_key(seeds[0]): {
                        "status": "retryable_error",
                        "reason": "HTTPError",
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            }
            self._write_payload(state_path, state)
            policy = self._policy()
            called = []

            def fake_request(url, **kwargs):
                called.append(url)
                path = urlsplit(url).path
                html = f'<html><a href="{path}/article">Article</a></html>'.encode()
                return html, 200, {"Content-Type": "text/html; charset=utf-8"}, policy

            with patch.object(expansion, "policy_for_host", return_value=policy), patch.object(
                expansion, "_purpose_path_allowed", return_value=True
            ), patch.object(expansion, "request_source_bytes", side_effect=fake_request):
                first = expansion.run_batch(queue_path, state_path, 2, index_path)
                self.assertEqual(first["last_batch_size"], 2)
                self.assertEqual(first["last_batch_processed"], 2)
                self.assertEqual(called, [seeds[1]["source_url"], seeds[2]["source_url"]])
                second = expansion.run_batch(queue_path, state_path, 2, index_path)

            self.assertEqual(second["last_batch_size"], 0)
            self.assertEqual(len(called), 2)

    def test_batch_round_robins_across_reviewed_source_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.json"
            state_path = root / "state.json.gz"
            index_path = root / "manifest.json"
            index_raw = self._write_payload(index_path, {"total_pages": 4, "updated_at": 1795429785})
            seeds = [self._queue_row(i) for i in range(2)]
            for i in range(2):
                seeds.append(dict(
                    self._queue_row(i + 2),
                    source_url=f"https://nature.com/journals/demo{i}/articles",
                    terms_url="https://www.nature.com/info/terms-of-use",
                ))
            queue = {
                "status": "source_review_backlog_not_a_fetch_queue",
                "source_index_pages": 4,
                "source_index_updated_at": "2026-11-23T10:29:45Z",
                "source_index_sha256": hashlib.sha256(index_raw).hexdigest(),
                "candidate_urls": seeds,
            }
            self._write_payload(queue_path, queue)
            policy = self._policy()
            called = []

            def fake_request(url, **kwargs):
                called.append(url)
                path = urlsplit(url).path
                html = f'<html><a href="{path}/article">Article</a></html>'.encode()
                return html, 200, {"Content-Type": "text/html; charset=utf-8"}, policy

            with patch.object(expansion, "policy_for_host", return_value=policy), patch.object(
                expansion, "_purpose_path_allowed", return_value=True
            ), patch.object(expansion, "request_source_bytes", side_effect=fake_request):
                result = expansion.run_batch(queue_path, state_path, 2, index_path)

            self.assertEqual(result["last_batch_size"], 2)
            self.assertEqual(len(called), 2)
            self.assertEqual({urlsplit(url).hostname for url in called}, {"frontiersin.org", "nature.com"})

    def test_merges_canonical_record_memberships_and_provenance(self):
        html = '<a href="/journals/demo/articles/one">Article One</a>'
        first = expansion.extract_child_records(
            html, "https://frontiersin.org/journals/demo",
            ["catalog:one"], {"terms_url": "https://example.org/terms"}, "2026-11-23T00:00:00Z"
        )[0]
        second = expansion.extract_child_records(
            html, "https://frontiersin.org/journals/demo",
            ["catalog:two"], {"terms_url": "https://example.org/terms"}, "2026-11-23T00:01:00Z"
        )[0]
        self.assertEqual(first["record_id"], second["record_id"])
        expansion._merge_record(first, second)
        self.assertEqual(first["catalog_ids"], ["catalog:one", "catalog:two"])
        self.assertEqual(len(first["source_provenance"]), 2)
        self.assertEqual(first["last_seen_at"], "2026-11-23T00:01:00Z")

    def test_workflow_uses_validated_catalog_review_queue_not_page_profiles(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "catalog-seed-expansion.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("source-host-review-queue.json", workflow)
        self.assertIn('"data/page-derived-catalogs/report.json"', workflow)
        self.assertIn('"data/page-derived-catalogs/source-host-review-queue.json"', workflow)
        self.assertIn('"data/catalog-landing-page-robots.json"', workflow)
        self.assertIn("public-search/manifest.json", workflow)
        self.assertIn("_queue_snapshot_compatible_with_current_index", workflow)
        self.assertNotIn("page-catalog-seeds.json.gz", workflow)
        retry_block = workflow.split("for attempt in 1 2 3", 1)[1]
        # Binary checkpoints cannot be text-rebased. Rebuild the same bounded,
        # idempotent batch from the newest main ledger after a push race.
        self.assertIn("git reset --hard origin/main", retry_block)
        self.assertIn("python crawler/catalog_seed_expansion.py --limit 10", retry_block)
        self.assertIn("python database/stage_page_catalog_memberships.py", retry_block)
        self.assertIn("python -m unittest database.test_import_page_catalog_memberships -v", retry_block)
        self.assertIn("git push origin HEAD:main", retry_block)
        self.assertNotIn("git rebase origin/main", retry_block)

    def test_batch_limit_cannot_exceed_ten(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            expansion.run_batch(Path("missing.json"), Path("state.json.gz"), 11)


if __name__ == "__main__":
    unittest.main()
