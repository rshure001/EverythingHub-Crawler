import inspect
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import urlsplit

from catalog_discovery import build_catalog, normalize_dataset, SOURCES, EXCLUDED_SOURCES
from catalog_source_policy import policy_for_host

class CatalogDiscoveryTests(unittest.TestCase):
    def test_dataset_keeps_parent_catalog_and_bulk_resources(self):
        item = normalize_dataset({
            "name": "transport-data",
            "title": "Transport Data",
            "notes": "A public dataset",
            "organization": {"title": "Transport Agency"},
            "tags": [{"name": "transport"}],
            "license_id": "cc-by", "license_title": "Creative Commons Attribution", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "rights": "Open use. Must provide the source", "isopen": True, "private": False,
            "resources": [
                {"name": "CSV download", "description": "Dataset distribution", "format": "csv", "mimetype": "text/csv", "rights": "Open use. Must provide the source", "url": "https://downloads.example.org/data.csv", "size": 1200},
                {"name": "Unsafe", "format": "zip", "url": "http://downloads.example.org/data.zip"},
            ],
        }, SOURCES[0])
        self.assertEqual(item["catalog_id"], "canada-open-data")
        self.assertEqual(item["resource_count"], 1)
        self.assertEqual(item["license"]["id"], "cc-by")
        self.assertEqual(item["license"]["rights"], "Open use. Must provide the source")
        self.assertTrue(item["license"]["is_open"])
        self.assertFalse(item["license"]["private"])
        self.assertEqual(item["resources"][0]["format"], "CSV")
        self.assertEqual(item["resources"][0]["media_type"], "text/csv")
        self.assertEqual(item["resources"][0]["rights"], "Open use. Must provide the source")
        self.assertTrue(item["landing_url"].startswith("https://open.canada.ca/data/en/dataset/"))


    def test_private_or_inactive_dataset_is_skipped(self):
        private_item = normalize_dataset({
            "name": "private-data", "title": "Private Data", "private": True, "state": "active"
        }, SOURCES[0])
        inactive_item = normalize_dataset({
            "name": "draft-data", "title": "Draft Data", "private": False, "state": "draft"
        }, SOURCES[0])
        self.assertIsNone(private_item)
        self.assertIsNone(inactive_item)

    def test_missing_dataset_identifier_is_skipped(self):
        self.assertIsNone(normalize_dataset({"title": "No ID"}, SOURCES[0]))


    def test_default_discovery_batch_is_bounded_by_each_reviewed_host_budget(self):
        max_pages = inspect.signature(build_catalog).parameters["max_pages"].default
        self.assertEqual(max_pages, 40)
        for source in SOURCES:
            host = urlsplit(source["base"]).hostname
            policy = policy_for_host(host)
            self.assertTrue(policy["discovery_allowed"], source["id"])
            self.assertGreaterEqual(policy["max_requests_per_run"], max_pages, source["id"])
            self.assertGreaterEqual(policy["min_interval_seconds"], 1.0, source["id"])

        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "catalog-first-discovery.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('default: "40"', workflow)
        self.assertEqual(workflow.count("inputs.max_pages || '40'"), 2)

    def test_discovery_reports_total_available_and_respects_source_request_budget(self):
        policy = {
            "discovery_allowed": True,
            "terms_url": "https://catalog.data.gov/terms",
            "terms_status": "reviewed_per_dataset",
            "reviewed_at": "2026-11-21T00:00:00Z",
            "attribution_required": "per_dataset",
            "per_dataset_rights_required": True,
            "max_requests_per_run": 80,
        }
        page_one = {
            "success": True,
            "result": {"count": 3, "results": [
                {"name": "dataset-one", "title": "Dataset One"},
                {"name": "dataset-two", "title": "Dataset Two"},
            ]},
        }
        page_two = {
            "success": True,
            "result": {"count": 3, "results": [
                {"name": "dataset-three", "title": "Dataset Three"},
            ]},
        }
        with patch("catalog_discovery.policy_for_host", return_value=policy), patch(
            "catalog_discovery._request_json", side_effect=[page_one, page_two]
        ) as request:
            result = __import__("catalog_discovery").discover_source(SOURCES[0], rows=2, max_pages=40)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["available_dataset_count"], 3)
        self.assertEqual(result["datasets_discovered"], 3)
        self.assertEqual(result["pages_requested"], 2)
        self.assertEqual(result["page_limit_applied"], 40)

        restricted_budget = {**policy, "max_requests_per_run": 1}
        with patch("catalog_discovery.policy_for_host", return_value=restricted_budget), patch(
            "catalog_discovery._request_json", return_value=page_one
        ) as request:
            capped = __import__("catalog_discovery").discover_source(SOURCES[0], rows=2, max_pages=40)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(capped["page_limit_applied"], 1)
        self.assertEqual(capped["pages_requested"], 1)
        self.assertEqual(capped["available_dataset_count"], 3)

    def test_max_pages_cannot_exceed_policy_envelope(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 80"):
            build_catalog(max_pages=81)

    def test_catalog_sources_are_https(self):
        self.assertTrue(all(source["base"].startswith("https://") for source in SOURCES))
        self.assertNotIn("australia-data-gov", {source["id"] for source in SOURCES})
        self.assertNotIn("us-data-gov", {source["id"] for source in SOURCES})
        excluded = {source["id"]: source for source in EXCLUDED_SOURCES}
        self.assertIn("us-data-gov", excluded)
        self.assertIn("free personal API key", excluded["us-data-gov"]["reason"])


    @staticmethod
    def _previous_dataset(name, generation):
        item = normalize_dataset({"name": name, "title": name}, SOURCES[0])
        item["last_seen_generation"] = generation
        return item

    @staticmethod
    def _reviewed_policy():
        return {
            "discovery_allowed": True,
            "terms_url": "https://example.org/terms",
            "terms_status": "reviewed_per_dataset",
            "reviewed_at": "2026-11-21",
            "attribution_required": "per_dataset",
            "per_dataset_rights_required": True,
            "max_requests_per_run": 80,
            "min_interval_seconds": 1,
        }

    def test_discovery_resumes_from_saved_offset_and_merges_previous_records(self):
        from catalog_discovery import discover_source
        previous = [
            self._previous_dataset("one", 1),
            self._previous_dataset("two", 1),
        ]
        page_two = {
            "success": True,
            "result": {"count": 4, "results": [
                {"name": "three", "title": "Three"},
                {"name": "four", "title": "Four"},
            ]},
        }
        with patch("catalog_discovery.policy_for_host", return_value=self._reviewed_policy()), patch(
            "catalog_discovery._request_json", return_value=page_two
        ) as request:
            result = discover_source(
                SOURCES[0], rows=2, max_pages=1, start_offset=2,
                previous_datasets=previous, scan_generation=1,
                previous_available_dataset_count=4,
                previous_pages_requested_total=1,
            )
        self.assertIn("start=2", request.call_args.args[0])
        self.assertEqual(result["pages_requested"], 1)
        self.assertEqual(result["pages_requested_total"], 2)
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["next_start"], 0)
        self.assertEqual(result["datasets_discovered"], 4)
        self.assertEqual(
            {item["dataset_id"] for item in result["datasets"]},
            {"one", "two", "three", "four"},
        )

    def test_completed_scan_prunes_records_not_seen_in_new_generation(self):
        from catalog_discovery import discover_source
        previous = [
            self._previous_dataset("one", 1),
            self._previous_dataset("two", 1),
            self._previous_dataset("removed", 1),
        ]
        refreshed_page = {
            "success": True,
            "result": {"count": 2, "results": [
                {"name": "one", "title": "One refreshed"},
                {"name": "two", "title": "Two refreshed"},
            ]},
        }
        with patch("catalog_discovery.policy_for_host", return_value=self._reviewed_policy()), patch(
            "catalog_discovery._request_json", return_value=refreshed_page
        ):
            result = discover_source(
                SOURCES[0], rows=2, max_pages=1, start_offset=0,
                previous_datasets=previous, scan_generation=2,
                previous_available_dataset_count=3,
            )
        self.assertTrue(result["scan_complete"])
        self.assertEqual(
            {item["dataset_id"] for item in result["datasets"]},
            {"one", "two"},
        )
        self.assertEqual(result["datasets_discovered"], 2)

    def test_failed_page_advances_only_past_successful_pages(self):
        from catalog_discovery import discover_source
        first_page = {
            "success": True,
            "result": {"count": 6, "results": [
                {"name": "one", "title": "One"},
                {"name": "two", "title": "Two"},
            ]},
        }
        with patch("catalog_discovery.policy_for_host", return_value=self._reviewed_policy()), patch(
            "catalog_discovery._request_json", side_effect=[first_page, TimeoutError("temporary timeout")]
        ):
            result = discover_source(
                SOURCES[0], rows=2, max_pages=2, start_offset=0,
                scan_generation=2,
            )
        self.assertEqual(result["pages_requested"], 1)
        self.assertEqual(result["next_start"], 2)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["datasets_discovered"], 2)
        self.assertTrue(any("offset 2" in error for error in result["errors"]))

    def test_build_catalog_migrates_legacy_snapshot_to_resumable_offset(self):
        source = SOURCES[0]
        previous = {
            "schema_version": 1,
            "strategy": "catalog-first-policy-gated",
            "catalogs": [{
                "id": source["id"],
                "pages_requested": 1,
                "available_dataset_count": 4,
                "datasets_discovered": 2,
                "resources_discovered": 0,
                "scan_complete": False,
            }],
            "datasets": [
                self._previous_dataset("one", 0),
                self._previous_dataset("two", 0),
            ],
        }
        page_two = {
            "success": True,
            "result": {"count": 4, "results": [
                {"name": "three", "title": "Three"},
                {"name": "four", "title": "Four"},
            ]},
        }
        with patch("catalog_discovery.SOURCES", (source,)), patch(
            "catalog_discovery.policy_for_host", return_value=self._reviewed_policy()
        ), patch("catalog_discovery._request_json", return_value=page_two) as request:
            result = build_catalog(rows=2, max_pages=1, previous=previous)
        self.assertIn("start=2", request.call_args.args[0])
        self.assertEqual(result["datasets_discovered"], 4)
        progress = result["scan_progress"][source["id"]]
        self.assertTrue(progress["scan_complete"])
        self.assertEqual(progress["next_start"], 0)

    def test_previous_manifest_loader_fails_closed_on_corrupt_snapshot(self):
        import tempfile
        from catalog_discovery import load_previous_manifest
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogs.json.gz"
            path.write_bytes(b"not-a-gzip-file")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                load_previous_manifest(path)

if __name__ == "__main__":
    unittest.main()
