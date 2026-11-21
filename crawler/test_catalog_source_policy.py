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
        self.assertEqual(frontiers["allowed_catalog_paths"], ["/journals", "/articles"])
        self.assertEqual(frontiers["min_interval_seconds"], 3)

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
        self.assertEqual(paths, ["/journals", "/articles"])
        self.assertTrue(source["metadata_only"])
        self.assertEqual(source["rights_scope"], "per_article_license")

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

    def test_openlibrary_is_not_an_api_discovery_source(self):
        with self.assertRaises(policy.SourcePolicyError):
            policy.authorize_source_url("https://openlibrary.org/subjects/science.json?limit=500")


if __name__ == "__main__":
    unittest.main()
