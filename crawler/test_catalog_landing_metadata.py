import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import catalog_landing_metadata as metadata
from catalog_host_probe import _landing_policy_fingerprint


def sample_html():
    payload = {
        "@context": "https://schema.org",
        "@graph": [{
            "@type": "ScholarlyArticle",
            "headline": "A bounded metadata extraction study",
            "identifier": {
                "@type": "PropertyValue",
                "propertyID": "DOI",
                "value": "https://doi.org/10.3389/fexample.2026.12345",
            },
            "author": [
                {"@type": "Person", "name": "A. Researcher"},
                {"@type": "Person", "name": "B. Scientist"},
            ],
            "isPartOf": {"@type": "Periodical", "name": "Frontiers in Example"},
            "datePublished": "2026-10-01",
            "license": "https://creativecommons.org/licenses/by/4.0/",
            "publisher": {"@type": "Organization", "name": "Frontiers Media SA"},
            "url": "https://www.frontiersin.org/articles/10.3389/fexample.2026.12345/full",
            "abstract": "THIS ABSTRACT MUST NEVER BE STORED.",
            "image": "https://www.frontiersin.org/images/do-not-store.jpg",
        }],
    }
    return '<html><script type="application/ld+json">' + json.dumps(payload) + "</script></html>"


def source_policy():
    return {
        "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
        "terms_status": "reviewed_per_dataset",
        "reviewed_at": "2026-11-21",
        "metadata_only": True,
        "allowed_metadata_fields": [
            "title", "authors", "doi", "journal", "publication_date",
            "article_url", "license", "publisher",
        ],
        "prohibited_content": ["full_text", "images", "supplementary_files", "bulk_assets"],
        "max_requests_per_run": 40,
    }


def approved_robots_row(url="https://frontiersin.org/journals/example/articles", catalog_id="catalog:test"):
    row = {
        "source_url": url,
        "source_host": "frontiersin.org",
        "catalog_ids": [catalog_id],
        "catalog_count": 1,
        "source_access_mode": "catalog_landing_page",
        "review_status": "policy_preapproved_robots_recheck_required",
        "terms_url": "https://www.frontiersin.org/legal/terms-and-conditions",
        "terms_status": "reviewed_per_dataset",
        "reviewed_at": "2026-11-21",
        "allowed_catalog_path_patterns": [
            "/articles", "/journals/*/articles",
            "/journals/*/sections/*", "/journals/*/sections/*/articles",
        ],
        "metadata_only": True,
        "allowed_metadata_fields": source_policy()["allowed_metadata_fields"],
        "prohibited_content": source_policy()["prohibited_content"],
        "max_response_bytes": 1_500_000,
        "min_interval_seconds": 3.0,
        "max_requests_per_run": 40,
        "fetch_authorized": False,
        "page_content_fetched": False,
        "page_requests": 0,
        "robots_check_performed": True,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "robots_allowed",
        "robots_allowed": True,
    }
    row["policy_fingerprint"] = _landing_policy_fingerprint(row)
    return row


class FrontiersMetadataTests(unittest.TestCase):
    def test_extracts_doi_and_allowlisted_metadata_without_abstract_or_media(self):
        records = metadata.extract_articles(
            sample_html(),
            source_url="https://frontiersin.org/journals/example/articles",
            catalog_ids=["catalog:test"],
            policy=source_policy(),
            fetched_at="2026-11-21T12:00:00Z",
        )
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["record_type"], "article")
        self.assertEqual(record["canonical_entity_key"], "doi:10.3389/fexample.2026.12345")
        self.assertEqual(record["attributes"]["journal"], "Frontiers in Example")
        self.assertEqual(record["attributes"]["authors"], ["A. Researcher", "B. Scientist"])
        self.assertEqual(record["attributes"]["license_classification"], "cc_by_evidence")
        self.assertNotIn("abstract", record)
        self.assertNotIn("abstract", record["attributes"])
        self.assertNotIn("image", record["attributes"])
        self.assertFalse(record["attributes"]["public_search_allowed"])
        self.assertFalse(record["attributes"]["commercial_eligible"])
        self.assertFalse(record["attributes"]["full_text_stored"])

    def test_global_doi_id_and_memberships_deduplicate_across_catalogs(self):
        first = metadata.extract_articles(
            sample_html(),
            source_url="https://frontiersin.org/journals/example/articles",
            catalog_ids=["catalog:journal"],
            policy=source_policy(),
        )[0]
        second = metadata.extract_articles(
            sample_html(),
            source_url="https://frontiersin.org/journals/example/sections/research/articles",
            catalog_ids=["catalog:section"],
            policy=source_policy(),
        )[0]
        self.assertEqual(first["record_id"], second["record_id"])
        metadata.merge_article_record(first, second)
        self.assertEqual(first["catalog_ids"], ["catalog:journal", "catalog:section"])
        self.assertEqual(len(first["source_listing_urls"]), 2)

    def test_run_fetches_only_fresh_robots_approved_rows_and_stages_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_path = root / "robots.json"
            manifest_path = root / "catalog-manifest.json"
            registry_path = root / "registry.json.gz"
            output_path = root / "frontiers.json.gz"
            manifest = {
                "analysis_version": 13,
                "source_index_pages": 151584,
                "source_index_sha256": "test-index-sha",
            }
            row = approved_robots_row()
            ledger_path.write_text(json.dumps({
                "status": "robots_preflight_only_not_fetch_authorization",
                **manifest,
                "results": [row],
            }), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = {"catalogs": [{
                "catalog_id": "catalog:test",
                "catalog_definition": True,
                "title": "Frontiers in Example",
                "catalog_type": "knowledge_base",
                "source_url": row["source_url"],
            }]}
            with patch.object(metadata, "load_verified_registry", return_value=(manifest, registry)), \
                 patch.object(metadata, "request_source_bytes", return_value=(
                     sample_html().encode("utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}, source_policy()
                 )) as request:
                metrics = metadata.run(
                    limit=1,
                    robots_ledger_path=ledger_path,
                    catalog_manifest_path=manifest_path,
                    registry_path=registry_path,
                    output_path=output_path,
                )
            request.assert_called_once()
            self.assertEqual(metrics["pages_fetched_this_run"], 1)
            self.assertEqual(metrics["unique_records"], 1)
            self.assertEqual(metrics["records_published"], 0)
            self.assertEqual(metrics["public_search_allowed_records"], 0)
            output = json.loads(gzip.decompress(output_path.read_bytes()).decode("utf-8"))
            self.assertEqual(output["status"], "candidate_metadata_staging_not_production")
            self.assertEqual(output["records"][0]["canonical_entity_key"], "doi:10.3389/fexample.2026.12345")
            self.assertNotIn("abstract", output["records"][0]["attributes"])
            self.assertFalse(output["source_pages"][0]["content_stored"])
            self.assertEqual(output["source_pages"][0]["abstracts_stored"], 0)

    def test_legacy_or_incomplete_robots_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_path = root / "robots.json"
            manifest_path = root / "catalog-manifest.json"
            registry_path = root / "registry.json.gz"
            output_path = root / "frontiers.json.gz"
            manifest = {
                "analysis_version": 13,
                "source_index_pages": 151584,
                "source_index_sha256": "test-index-sha",
            }
            row = approved_robots_row()
            row.pop("review_status")
            row["policy_fingerprint"] = _landing_policy_fingerprint(row)
            ledger_path.write_text(json.dumps({
                "status": "robots_preflight_only_not_fetch_authorization",
                **manifest,
                "results": [row],
            }), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = {"catalogs": [{
                "catalog_id": "catalog:test",
                "catalog_definition": True,
                "title": "Frontiers in Example",
                "catalog_type": "knowledge_base",
                "source_url": row["source_url"],
            }]}
            with patch.object(metadata, "load_verified_registry", return_value=(manifest, registry)), \
                 patch.object(metadata, "request_source_bytes") as request:
                metrics = metadata.run(
                    limit=1,
                    robots_ledger_path=ledger_path,
                    catalog_manifest_path=manifest_path,
                    registry_path=registry_path,
                    output_path=output_path,
                )
            request.assert_not_called()
            self.assertEqual(metrics["pages_attempted_this_run"], 0)
            self.assertEqual(metrics["records_staged"], 0)

    def test_stale_manifest_mismatch_prevents_all_fetches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_path = root / "robots.json"
            manifest_path = root / "catalog-manifest.json"
            registry_path = root / "registry.json.gz"
            output_path = root / "frontiers.json.gz"
            ledger_path.write_text(json.dumps({
                "status": "robots_preflight_only_not_fetch_authorization",
                "analysis_version": 12,
                "source_index_pages": 100,
                "source_index_sha256": "old-sha",
                "results": [approved_robots_row()],
            }), encoding="utf-8")
            manifest = {
                "analysis_version": 13,
                "source_index_pages": 101,
                "source_index_sha256": "new-sha",
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(metadata, "load_verified_registry", return_value=(manifest, {"catalogs": []})), \
                 patch.object(metadata, "request_source_bytes") as request:
                with self.assertRaisesRegex(ValueError, "stale or does not match"):
                    metadata.run(
                        limit=1,
                        robots_ledger_path=ledger_path,
                        catalog_manifest_path=manifest_path,
                        registry_path=registry_path,
                        output_path=output_path,
                    )
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
