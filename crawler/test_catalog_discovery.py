import inspect
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import urlsplit

from catalog_discovery import build_catalog, normalize_dataset, SOURCES
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
            "resources": [
                {"name": "CSV download", "format": "csv", "url": "https://downloads.example.org/data.csv", "size": 1200},
                {"name": "Unsafe", "format": "zip", "url": "http://downloads.example.org/data.zip"},
            ],
        }, SOURCES[0])
        self.assertEqual(item["catalog_id"], "us-data-gov")
        self.assertEqual(item["resource_count"], 1)
        self.assertEqual(item["license"]["id"], "cc-by")
        self.assertEqual(item["resources"][0]["format"], "CSV")
        self.assertTrue(item["landing_url"].startswith("https://catalog.data.gov/dataset/"))

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

if __name__ == "__main__":
    unittest.main()
