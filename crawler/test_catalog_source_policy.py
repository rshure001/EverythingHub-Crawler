import unittest
from unittest.mock import patch

import catalog_source_policy as policy


class CatalogSourcePolicyTests(unittest.TestCase):
    def setUp(self):
        policy.reset_policy_state_for_tests()

    def test_registry_defaults_to_deny_for_unreviewed_and_restricted_hosts(self):
        self.assertIsNone(policy.policy_for_host("unknown.example"))
        self.assertFalse(policy.policy_for_host("data.gov.au")["discovery_allowed"])
        self.assertFalse(policy.policy_for_host("openlibrary.org")["discovery_allowed"])
        self.assertFalse(policy.policy_for_host("catalog.data.gov")["discovery_allowed"])
        swiss = policy.policy_for_host("opendata.swiss")
        self.assertTrue(swiss["discovery_allowed"])
        self.assertEqual(swiss["terms_status"], "reviewed_per_dataset")
        self.assertEqual(swiss["max_requests_per_run"], 40)
        self.assertEqual(swiss["min_interval_seconds"], 2.0)
        frontiers = policy.policy_for_host("frontiersin.org")
        self.assertTrue(frontiers["discovery_allowed"])
        self.assertTrue(frontiers["metadata_only"])
        self.assertTrue(frontiers["per_dataset_rights_required"])
        self.assertEqual(frontiers["allowed_api_paths"], [])
        self.assertEqual(frontiers["allowed_catalog_paths"], [])
        self.assertEqual(frontiers["allowed_catalog_path_patterns"], [
            "/articles",
            "/journals/*/articles",
            "/journals/*/sections/*",
            "/journals/*/sections/*/articles",
        ])
        self.assertEqual(frontiers["min_interval_seconds"], 3)
        self.assertEqual(frontiers["max_response_bytes"], 1500000)
        self.assertIn("doi", frontiers["allowed_metadata_fields"])
        self.assertIn("full_text", frontiers["prohibited_content"])

    def test_canada_open_data_api_is_reviewed_metadata_only_and_rate_limited(self):
        canada = policy.policy_for_host("open.canada.ca")
        self.assertIsNotNone(canada)
        self.assertTrue(canada["discovery_allowed"])
        self.assertEqual(canada["terms_status"], "reviewed_per_dataset")
        self.assertTrue(canada["metadata_only"])
        self.assertTrue(canada["per_dataset_rights_required"])
        self.assertTrue(canada["attribution_required"])
        self.assertEqual(
            canada["terms_url"],
            "https://open.canada.ca/en/open-government-licence-canada/",
        )
        self.assertTrue(canada["reviewed_at"])
        self.assertEqual(
            canada["allowed_api_paths"],
            ["/data/en/api/3/action/package_search"],
        )
        self.assertEqual(canada["max_requests_per_run"], 80)
        self.assertGreaterEqual(canada["min_interval_seconds"], 1.0)

        parser = object()
        with patch.object(policy, "load_robots_policy", return_value=parser), \
             patch.object(policy, "robots_can_fetch", return_value=True), \
             patch.object(policy, "effective_host_delay", return_value=0.5):
            source, loaded_parser, delay, paths = policy.authorize_source_url(
                "https://open.canada.ca/data/en/api/3/action/package_search"
            )
        self.assertIs(loaded_parser, parser)
        self.assertEqual(delay, 1.0)
        self.assertIn("/data/en/api/3/action/package_search", paths)
        self.assertEqual(source["terms_status"], "reviewed_per_dataset")

    def test_unreviewed_host_is_rejected_before_robots_fetch(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url("https://data.gov.au/data/api/3/action/package_search")
            load_robots.assert_not_called()

    def test_path_outside_allowlist_is_rejected_before_robots_fetch(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url("https://opendata.swiss/openapi.json")
            load_robots.assert_not_called()

    def test_robots_denial_fails_closed(self):
        with patch.object(policy, "load_robots_policy", return_value=object()), \
             patch.object(policy, "robots_can_fetch", return_value=False):
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url("https://opendata.swiss/api/3/action/package_search")

    def test_rate_delay_uses_strictest_source_and_robots_interval(self):
        parser = object()
        with patch.object(policy, "load_robots_policy", return_value=parser), \
             patch.object(policy, "robots_can_fetch", return_value=True), \
             patch.object(policy, "effective_host_delay", return_value=2.5):
            source, loaded_parser, delay, paths = policy.authorize_source_url(
                "https://opendata.swiss/api/3/action/package_search"
            )
        self.assertIs(loaded_parser, parser)
        self.assertEqual(delay, 2.5)
        self.assertIn("/api/3/action/package_search", paths)
        self.assertEqual(source["terms_status"], "reviewed_per_dataset")


    def test_frontiers_catalog_landing_page_requires_its_separate_allowlist_and_robots(self):
        parser = object()
        with patch.object(policy, "load_robots_policy", return_value=parser), \
             patch.object(policy, "robots_can_fetch", return_value=True), \
             patch.object(policy, "effective_host_delay", return_value=3.5):
            source, loaded_parser, delay, paths = policy.authorize_source_url(
                "https://frontiersin.org/journals/insect-science/articles",
                purpose="landing_page",
            )
        self.assertIs(loaded_parser, parser)
        self.assertEqual(delay, 3.5)
        self.assertEqual(paths, [])
        self.assertTrue(source["metadata_only"])
        self.assertEqual(source["rights_scope"], "per_article_license")


    def test_frontiers_nested_section_catalog_path_is_allowed(self):
        parser = object()
        with patch.object(policy, "load_robots_policy", return_value=parser), \
             patch.object(policy, "robots_can_fetch", return_value=True), \
             patch.object(policy, "effective_host_delay", return_value=3.0):
            source, _, _, _ = policy.authorize_source_url(
                "https://frontiersin.org/journals/insect-science/sections/plant-pathology/articles",
                purpose="landing_page",
            )
        self.assertTrue(source["metadata_only"])


    def test_frontiers_encoded_path_separator_is_rejected_before_robots(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url(
                    "https://frontiersin.org/journals/insect%2Fscience/articles",
                    purpose="landing_page",
                )
            load_robots.assert_not_called()

    def test_frontiers_individual_article_fulltext_path_is_rejected_before_robots(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url(
                    "https://frontiersin.org/journals/insect-science/articles/10.3389/fmicb.2021.123456/full",
                    purpose="landing_page",
                )
            load_robots.assert_not_called()

    def test_frontiers_landing_page_path_outside_allowlist_is_rejected_before_robots(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url(
                    "https://frontiersin.org/images/logo.png",
                    purpose="landing_page",
                )
            load_robots.assert_not_called()

    def test_frontiers_catalog_page_is_not_accidentally_authorized_as_an_api(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url(
                    "https://frontiersin.org/journals/insect-science/articles",
                    purpose="discovery",
                )
            load_robots.assert_not_called()

    def test_frontiers_landing_page_robots_denial_fails_closed(self):
        with patch.object(policy, "load_robots_policy", return_value=object()), \
             patch.object(policy, "robots_can_fetch", return_value=False):
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url(
                    "https://frontiersin.org/journals/testfieldhumanities/articles",
                    purpose="landing_page",
                )


    def test_frontiers_landing_page_policy_requires_metadata_schema_before_robots(self):
        source = policy.policy_for_host("frontiersin.org")
        source.pop("allowed_metadata_fields")
        with patch.object(policy, "policy_for_host", return_value=source), \
             patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url(
                    "https://frontiersin.org/journals/insect-science/articles",
                    purpose="landing_page",
                )
            load_robots.assert_not_called()

    def test_landing_page_request_requires_html_and_uses_policy_size_limit(self):
        class Response:
            status = 200
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://frontiersin.org/journals/insect-science/articles"

            def read(self, amount):
                return b"x" * amount

        class Opener:
            def open(self, request, timeout):
                return Response()

        source = policy.policy_for_host("frontiersin.org")
        parser = object()
        with patch.object(policy, "authorize_source_url", return_value=(source, parser, 0.0, [])), \
             patch.object(policy, "build_pinned_opener", return_value=Opener()), \
             patch.object(policy, "robots_can_fetch", return_value=True):
            with self.assertRaises(policy.SourcePolicyError):
                policy.request_source_bytes(
                    "https://frontiersin.org/journals/insect-science/articles",
                    purpose="landing_page",
                    accept="application/json",
                )
            with self.assertRaises(policy.SourcePolicyError):
                policy.request_source_bytes(
                    "https://frontiersin.org/journals/insect-science/articles",
                    purpose="landing_page",
                    accept="text/html",
                )

    def test_openlibrary_is_not_an_api_discovery_source(self):
        with self.assertRaises(policy.SourcePolicyError):
            policy.authorize_source_url("https://openlibrary.org/subjects/science.json?limit=500")


    def test_commons_video_api_allows_only_metadata_search_endpoint(self):
        source = policy.policy_for_host("commons.wikimedia.org")
        self.assertIsNotNone(source)
        self.assertTrue(source["metadata_only"])
        self.assertTrue(source["per_dataset_rights_required"])
        self.assertEqual(source["allowed_api_paths"], ["/w/api.php"])
        with patch.object(policy, "load_robots_policy", return_value=object()), \
             patch.object(policy, "robots_can_fetch", return_value=True), \
             patch.object(policy, "effective_host_delay", return_value=0.0):
            authorized, parser, delay, allowed_paths = policy.authorize_source_url(
                "https://commons.wikimedia.org/w/api.php?action=query",
                purpose="discovery",
            )
        self.assertEqual(authorized["terms_status"], "reviewed_per_dataset")
        self.assertEqual(allowed_paths, ["/w/api.php"])
        self.assertGreaterEqual(delay, 1.0)

    def test_commons_video_api_rejects_wiki_pages_and_asset_download_paths(self):
        with patch.object(policy, "load_robots_policy") as load_robots:
            for url in (
                "https://commons.wikimedia.org/wiki/File:Example.webm",
                "https://commons.wikimedia.org/w/index.php?title=File:Example.webm",
                "https://commons.wikimedia.org/download/file/123",
            ):
                with self.subTest(url=url):
                    with self.assertRaises(policy.SourcePolicyError):
                        policy.authorize_source_url(url, purpose="discovery")
            load_robots.assert_not_called()

    def test_commons_video_policy_requires_per_item_open_license_and_credit(self):
        source = policy.policy_for_host("commons.wikimedia.org")
        self.assertEqual(source["attribution_required"], "per_item")
        self.assertFalse(source["commercial_eligible"])
        self.assertIn("noncommercial_licenses", source["prohibited_content"])
        self.assertIn("no_derivatives_licenses", source["prohibited_content"])
        self.assertIn("imageinfo.extmetadata.LicenseShortName", source["allowed_metadata_fields"])

    def test_nasa_video_api_remains_disabled_when_preview_robots_are_unverifiable(self):
        source = policy.policy_for_host("images-api.nasa.gov")
        self.assertFalse(source["discovery_allowed"])
        self.assertIn("HTTP 403", source["blocked_reason"])
        with patch.object(policy, "load_robots_policy") as load_robots:
            with self.assertRaises(policy.SourcePolicyError):
                policy.authorize_source_url("https://images-api.nasa.gov/search?q=NASA", purpose="discovery")
            load_robots.assert_not_called()


if __name__ == "__main__":
    unittest.main()
