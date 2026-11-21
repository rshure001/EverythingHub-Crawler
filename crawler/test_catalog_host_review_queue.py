import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from catalog_host_review_queue import build_from_files, validate_review_queue


class CatalogHostReviewQueueTests(unittest.TestCase):
    def _fixture(self, root: Path):
        output = root / "page-derived-catalogs"
        output.mkdir()
        catalogs = [
            {
                "catalog_id": "catalog:one",
                "title": "Reviewed API collection",
                "catalog_type": "open_data",
                "record_type": "dataset",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 4,
                "source_page_count": 2,
                "source_host": "allowed.example",
                "source_url": "https://allowed.example/api/v1/catalog",
            },
            {
                "catalog_id": "catalog:two",
                "title": "Same reviewed API collection",
                "catalog_type": "open_data",
                "record_type": "dataset",
                "catalog_origin": "content_list_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "medium",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 3,
                "source_page_count": 1,
                "source_host": "allowed.example",
                "source_url": "https://allowed.example/api/v1/catalog#top",
            },
            {
                "catalog_id": "catalog:three",
                "title": "Unreviewed host collection",
                "catalog_type": "knowledge_base",
                "record_type": "article",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 2,
                "source_page_count": 1,
                "source_host": "unreviewed.example",
                "source_url": "https://unreviewed.example/api/catalog",
            },
            {
                "catalog_id": "catalog:four",
                "title": "Disabled host collection",
                "catalog_type": "knowledge_base",
                "record_type": "article",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 2,
                "source_page_count": 1,
                "source_host": "denied.example",
                "source_url": "https://denied.example/api/catalog",
            },
            {
                "catalog_id": "catalog:five",
                "title": "Path outside allowlist",
                "catalog_type": "open_data",
                "record_type": "dataset",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 2,
                "source_page_count": 1,
                "source_host": "allowed.example",
                "source_url": "https://allowed.example/catalog",
            },
            {
                "catalog_id": "catalog:frontiers",
                "title": "Frontiers Insect Science Articles",
                "catalog_type": "knowledge_base",
                "record_type": "article",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 12,
                "source_page_count": 1,
                "source_host": "frontiersin.org",
                "source_url": "https://frontiersin.org/journals/insect-science/articles",
            },
            {
                "catalog_id": "catalog:frontiers-fulltext",
                "title": "Frontiers Individual Full Text",
                "catalog_type": "knowledge_base",
                "record_type": "article",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 1,
                "source_page_count": 1,
                "source_host": "frontiersin.org",
                "source_url": "https://frontiersin.org/journals/insect-science/articles/10.3389/fmicb.2021.123456/full",
            },
            {
                "catalog_id": "catalog:frontiers-encoded",
                "title": "Frontiers Encoded Path",
                "catalog_type": "knowledge_base",
                "record_type": "article",
                "catalog_origin": "source_declared_collection",
                "catalog_definition": True,
                "evidence_strength": "high",
                "catalog_status": "candidate_needs_source_validation",
                "record_count": 1,
                "source_page_count": 1,
                "source_host": "frontiersin.org",
                "source_url": "https://frontiersin.org/journals/insect%2Fscience/articles",
            },
            {
                "catalog_id": "catalog:derived",
                "title": "Derived records are not landing-page definitions",
                "catalog_type": "knowledge_base",
                "record_type": "article",
                "catalog_definition": False,
                "record_count": 5,
                "source_page_count": 3,
                "source_host": "allowed.example",
                "source_url": "https://allowed.example/api/v1/derived",
            },
        ]
        registry = {
            "version": 1,
            "analysis_version": 7,
            "status": "candidate_staging_not_production",
            "metrics": {"unique_catalogs_discovered": len(catalogs)},
            "catalogs": catalogs,
        }
        raw = gzip.compress(json.dumps(registry, sort_keys=True).encode("utf-8"))
        registry_path = output / "registry.json.gz"
        registry_path.write_bytes(raw)
        manifest = {
            "version": 1,
            "integrity": "sha256",
            "analysis_version": 7,
            "source_index_pages": 100,
            "source_index_updated_at": "2026-11-21T00:00:00Z",
            "source_index_sha256": "source-index-sha",
            "catalog_count": len(catalogs),
            "record_count": 18,
            "metrics": registry["metrics"],
            "files": [{
                "file": "registry.json.gz",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "items": len(catalogs),
            }],
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        policy = {
            "schema_version": 1,
            "default_policy": "deny",
            "sources": {
                "allowed.example": {
                    "terms_url": "https://allowed.example/terms",
                    "terms_status": "reviewed_per_dataset",
                    "reviewed_at": "2026-11-20",
                    "discovery_allowed": True,
                    "metadata_only": True,
                    "per_dataset_rights_required": True,
                    "allowed_api_paths": ["/api/v1"],
                },
                "denied.example": {
                    "terms_url": "https://denied.example/terms",
                    "terms_status": "reviewed",
                    "reviewed_at": "2026-11-20",
                    "discovery_allowed": False,
                    "allowed_api_paths": ["/api"],
                },
                "frontiersin.org": {
                    "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
                    "terms_status": "reviewed_per_dataset",
                    "reviewed_at": "2026-11-21",
                    "discovery_allowed": True,
                    "metadata_only": True,
                    "per_dataset_rights_required": True,
                    "attribution_required": "per_article",
                    "rights_scope": "per_article_license",
                    "allowed_api_paths": [],
                    "allowed_catalog_paths": [],
                    "allowed_catalog_path_patterns": [
                        "/articles",
                        "/journals/*/articles",
                        "/journals/*/sections/*",
                        "/journals/*/sections/*/articles",
                    ],
                    "allowed_metadata_fields": ["title", "authors", "doi", "license"],
                    "prohibited_content": ["full_text", "images", "supplementary_files", "bulk_assets"],
                    "max_response_bytes": 1500000,
                    "min_interval_seconds": 3,
                    "max_requests_per_run": 40,
                    "policy_note": "Metadata-only fixture policy.",
                },
            },
        }
        policy_path = root / "catalog-source-terms.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        return manifest_path, registry_path, policy_path

    def test_only_declared_catalogs_enter_review_backlog_and_urls_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, registry_path, policy_path = self._fixture(Path(tmp))
            manifest, payload = build_from_files(manifest_path, registry_path, policy_path)
            summary = validate_review_queue(payload, manifest)
            self.assertEqual(summary["catalog_definition_candidates"], 8)
            self.assertEqual(summary["unique_source_urls"], 7)
            self.assertEqual(summary["policy_preapproved_urls_needing_runtime_robots_check"], 2)
            self.assertEqual(summary["policy_preapproved_catalog_page_urls_needing_runtime_robots_check"], 1)
            self.assertEqual(summary["path_not_allowlisted_urls"], 3)
            self.assertEqual(summary["policy_review_required_urls"], 2)
            self.assertEqual(summary["fetch_authorized_urls"], 0)
            self.assertEqual(payload["status"], "source_review_backlog_not_a_fetch_queue")
            shared = next(x for x in payload["candidate_urls"] if x["source_url"] == "https://allowed.example/api/v1/catalog")
            self.assertEqual(shared["catalog_count"], 2)
            self.assertTrue(shared["robots_recheck_required"])
            self.assertFalse(shared["fetch_authorized"])

    def test_policy_states_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, registry_path, policy_path = self._fixture(Path(tmp))
            _, payload = build_from_files(manifest_path, registry_path, policy_path)
            statuses = {x["catalog_id"]: x["review_status"] for x in payload["catalogs"]}
            self.assertEqual(statuses["catalog:one"], "policy_preapproved_robots_recheck_required")
            self.assertEqual(statuses["catalog:three"], "policy_review_required")
            self.assertEqual(statuses["catalog:four"], "discovery_disabled")
            self.assertEqual(statuses["catalog:five"], "path_not_allowlisted")
            self.assertEqual(statuses["catalog:frontiers"], "policy_preapproved_robots_recheck_required")
            self.assertEqual(statuses["catalog:frontiers-fulltext"], "path_not_allowlisted")
            self.assertEqual(statuses["catalog:frontiers-encoded"], "path_not_allowlisted")
            self.assertNotIn("catalog:derived", statuses)

    def test_registry_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, registry_path, policy_path = self._fixture(Path(tmp))
            registry_path.write_bytes(registry_path.read_bytes() + b"tamper")
            with self.assertRaises(ValueError):
                build_from_files(manifest_path, registry_path, policy_path)


    def test_frontiers_landing_page_is_preapproved_but_never_fetch_authorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, registry_path, policy_path = self._fixture(Path(tmp))
            _, payload = build_from_files(manifest_path, registry_path, policy_path)
            item = next(x for x in payload["catalogs"] if x["catalog_id"] == "catalog:frontiers")
            url = next(
                x for x in payload["candidate_urls"]
                if x["source_host"] == "frontiersin.org" and x["source_url"] == "https://frontiersin.org/journals/insect-science/articles"
            )
            self.assertEqual(item["source_access_mode"], "catalog_landing_page")
            self.assertTrue(item["metadata_only"])
            self.assertEqual(item["allowed_metadata_fields"], ["title", "authors", "doi", "license"])
            self.assertEqual(item["allowed_catalog_path_patterns"], [
                "/articles",
                "/journals/*/articles",
                "/journals/*/sections/*",
                "/journals/*/sections/*/articles",
            ])
            self.assertEqual(item["prohibited_content"], ["full_text", "images", "supplementary_files", "bulk_assets"])
            self.assertEqual(item["max_response_bytes"], 1500000)
            self.assertFalse(item["fetch_authorized"])
            self.assertTrue(item["robots_recheck_required"])
            self.assertEqual(url["source_access_mode"], "catalog_landing_page")
            self.assertFalse(url["fetch_authorized"])
            self.assertTrue(url["robots_recheck_required"])
            self.assertEqual(url["min_interval_seconds"], 3.0)
            self.assertEqual(url["max_requests_per_run"], 40)

    def test_no_fetch_or_robots_claims_can_be_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, registry_path, policy_path = self._fixture(Path(tmp))
            manifest, payload = build_from_files(manifest_path, registry_path, policy_path)
            payload["metrics"]["network_requests"] = 1
            with self.assertRaises(ValueError):
                validate_review_queue(payload, manifest)


if __name__ == "__main__":
    unittest.main()
