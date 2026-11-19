import unittest

from report import summarize_seed_coverage


class SeedCoverageTests(unittest.TestCase):
    def test_counts_unique_seed_hosts_and_reports_missing_hosts(self):
        seeds = [
            "https://www.example.com/",
            "https://data.example.gov/",
            "https://www.example.com/another-path",
        ]
        pages = [
            {"url": "https://www.example.com/page"},
            {"url": "https://sub.data.example.gov/page"},
            {"url": "ftp://data.example.gov/file"},
            {"title": "missing URL"},
            None,
        ]

        result = summarize_seed_coverage(seeds, pages)

        self.assertEqual(result["configured"], 2)
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(result["missing"], ["data.example.gov"])

    def test_normalizes_hostname_case_and_trailing_dot(self):
        result = summarize_seed_coverage(
            ["HTTPS://Example.ORG./"],
            [{"url": "https://example.org/article"}],
        )

        self.assertEqual(result, {"configured": 1, "indexed": 1, "missing": []})

    def test_ignores_invalid_and_non_http_seed_urls(self):
        result = summarize_seed_coverage(
            ["not a URL", "ftp://files.example.org/", "https:///missing-host"],
            [{"url": "https://files.example.org/document"}],
        )

        self.assertEqual(result, {"configured": 0, "indexed": 0, "missing": []})

    def test_empty_seed_list_has_no_missing_hosts(self):
        self.assertEqual(
            summarize_seed_coverage([], [{"url": "https://example.org/"}]),
            {"configured": 0, "indexed": 0, "missing": []},
        )


if __name__ == "__main__":
    unittest.main()
