import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import catalog_host_probe


class CumulativeProbeTests(unittest.TestCase):
    def test_batches_accumulate_host_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "hosts.json"
            output = root / "probes.json"
            source.write_text(json.dumps({
                "source_index_pages": 2,
                "host_candidates": [
                    {"host": "a.example", "source_pages": 1, "hint_counts": {"api": 1}},
                    {"host": "b.example", "source_pages": 1, "hint_counts": {"catalog": 1}},
                ],
            }), encoding="utf-8")

            def fake_probe(host, kind, path):
                return {"kind": kind, "url": "https://" + host + path,
                        "status": "responded", "evidence": "test_interface"}

            approved = {"discovery_allowed": True, "allowed_probe_paths": ["/api/3/action/package_search"]}
            with patch.object(catalog_host_probe, "policy_for_host", return_value=approved), \
                 patch.object(catalog_host_probe, "probe_one", side_effect=fake_probe), \
                 patch.object(catalog_host_probe.time, "sleep"):
                catalog_host_probe.run(source, output, limit=1, offset=0, pause=0)
                result = catalog_host_probe.run(source, output, limit=1, offset=1, pause=0)

            self.assertEqual(result["hosts_probed"], 2)
            self.assertEqual(result["verified_interface_hosts"], 2)
            self.assertEqual(result["verified_interfaces"], 2)
            self.assertEqual({x["host"] for x in result["results"]},
                             {"a.example", "b.example"})


    def _landing_candidate(self, url="https://frontiersin.org/journals/example/articles", status="policy_preapproved_robots_recheck_required"):
        return {
            "source_url": url,
            "source_host": "frontiersin.org",
            "catalog_ids": ["catalog:frontiers"],
            "catalog_count": 1,
            "review_status": status,
            "source_access_mode": "catalog_landing_page",
            "policy_preapproved": True,
            "metadata_only": True,
            "robots_recheck_required": True,
            "fetch_authorized": False,
            "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
            "terms_status": "reviewed_per_dataset",
            "reviewed_at": "2026-11-21",
            "allowed_catalog_path_patterns": ["/articles", "/journals/*/articles"],
            "allowed_metadata_fields": ["title", "authors", "doi", "license"],
            "prohibited_content": ["full_text", "images", "supplementary_files", "bulk_assets"],
            "max_response_bytes": 1_500_000,
            "min_interval_seconds": 3.0,
            "max_requests_per_run": 40,
        }

    def test_catalog_landing_page_robots_preflight_never_fetches_page_content(self):
        item = self._landing_candidate()
        policy = {"max_requests_per_run": 40}
        with patch.object(catalog_host_probe, "authorize_source_url", return_value=(policy, object(), 3.0, [])) as authorize:
            with patch.object(catalog_host_probe, "request_source_bytes") as request:
                result = catalog_host_probe.probe_catalog_landing_page_robots(item)
        authorize.assert_called_once_with(item["source_url"], purpose="landing_page")
        request.assert_not_called()
        self.assertEqual(result["status"], "robots_allowed")
        self.assertTrue(result["robots_allowed"])
        self.assertFalse(result["fetch_authorized"])
        self.assertFalse(result["page_content_fetched"])
        self.assertEqual(result["page_requests"], 0)

    def test_catalog_landing_page_robots_denial_fails_closed(self):
        from catalog_source_policy import SourcePolicyError
        item = self._landing_candidate()
        with patch.object(catalog_host_probe, "authorize_source_url", side_effect=SourcePolicyError("robots.txt disallows /journals/example/articles on frontiersin.org")):
            with patch.object(catalog_host_probe, "request_source_bytes") as request:
                result = catalog_host_probe.probe_catalog_landing_page_robots(item)
        request.assert_not_called()
        self.assertEqual(result["status"], "robots_denied")
        self.assertFalse(result["robots_allowed"])
        self.assertFalse(result["fetch_authorized"])

    def test_landing_robots_batch_is_bounded_cumulative_and_skips_unapproved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "source-host-review-queue.json"
            output_path = root / "robots.json"
            candidates = [self._landing_candidate(f"https://frontiersin.org/journals/j{i}/articles") for i in range(35)]
            candidates.append(self._landing_candidate("https://frontiersin.org/journals/not-allowed/articles", "path_not_allowlisted"))
            queue_path.write_text(json.dumps({
                "status": "source_review_backlog_not_a_fetch_queue",
                "analysis_version": 12,
                "source_index_pages": 100,
                "source_index_updated_at": "2026-11-21T00:00:00Z",
                "source_index_sha256": "source-index-sha",
                "metrics": {
                    "robots_checks_performed": 0,
                    "network_requests": 0,
                    "fetch_authorized_urls": 0,
                    "records_published": 0,
                    "catalog_definition_candidates": 0,
                    "unique_source_urls": len(candidates),
                },
                "catalogs": [],
                "hosts": [],
                "candidate_urls": candidates,
            }), encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({
                "analysis_version": 12,
                "source_index_pages": 100,
                "source_index_updated_at": "2026-11-21T00:00:00Z",
                "source_index_sha256": "source-index-sha",
            }), encoding="utf-8")
            with patch.object(catalog_host_probe, "authorize_source_url", return_value=({"max_requests_per_run": 40}, object(), 3.0, [])) as authorize:
                first = catalog_host_probe.run_landing_page_robots(queue_path, output_path, limit=30, recheck_seconds=86_400, manifest_path=manifest_path)
                self.assertEqual(first["last_batch_urls_checked"], 30)
                self.assertEqual(first["urls_checked"], 30)
                self.assertEqual(first["fetch_authorized_urls"], 0)
                self.assertEqual(first["page_content_fetched_urls"], 0)
                second = catalog_host_probe.run_landing_page_robots(queue_path, output_path, limit=30, recheck_seconds=86_400)
            self.assertEqual(second["last_batch_urls_checked"], 5)
            self.assertEqual(second["urls_checked"], 35)
            self.assertEqual(second["robots_allowed"], 35)
            self.assertEqual(second["fetch_authorized_urls"], 0)
            self.assertEqual(second["page_requests"], 0)
            self.assertEqual(authorize.call_count, 35)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_index_sha256"] = "stale-index-sha"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale or mismatched"):
                catalog_host_probe.run_landing_page_robots(queue_path, output_path, limit=30, recheck_seconds=86_400, manifest_path=manifest_path)

    def test_missing_review_queue_writes_safe_empty_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_path = root / "robots.json"
            result = catalog_host_probe.run_landing_page_robots(root / "missing.json", output_path)
            ledger = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "queue_missing")
        self.assertEqual(ledger["status"], "robots_preflight_only_not_fetch_authorization")
        self.assertEqual(ledger["fetch_authorized_urls"], 0)
        self.assertEqual(ledger["page_requests"], 0)

    def test_landing_robots_queue_rejects_fetch_authorized_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.json"
            output_path = root / "robots.json"
            queue_path.write_text(json.dumps({
                "status": "source_review_backlog_not_a_fetch_queue",
                "metrics": {"robots_checks_performed": 0, "network_requests": 1, "fetch_authorized_urls": 0, "records_published": 0},
                "candidate_urls": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-fetch queue"):
                catalog_host_probe.run_landing_page_robots(queue_path, output_path)


    def test_unreviewed_host_is_not_requested(self):
        with patch.object(catalog_host_probe, "policy_for_host", return_value=None), \
             patch.object(catalog_host_probe, "request_source_bytes") as request:
            result = catalog_host_probe.probe_one("unknown.example", "ckan_api", "/api/3/action/package_search")
        self.assertEqual(result["status"], "source_terms_unreviewed")
        request.assert_not_called()

    def test_robots_denial_is_reported_without_following_request(self):
        from catalog_source_policy import SourcePolicyError
        with patch.object(catalog_host_probe, "policy_for_host", return_value={"discovery_allowed": True}), \
             patch.object(catalog_host_probe, "request_source_bytes", side_effect=SourcePolicyError("robots.txt disallows")):
            result = catalog_host_probe.probe_one("catalog.data.gov", "ckan_api", "/api/3/action/package_search")
        self.assertEqual(result["status"], "source_policy_blocked")

if __name__ == "__main__":
    unittest.main()
