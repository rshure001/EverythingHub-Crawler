import unittest

from search_core import search, tokenize


PAGES = [
    {"url": "https://www.rolex.com/watches", "title": "Rolex watches",
     "description": "Discover precision Swiss watches.", "text": "Rolex watchmaking",
     "lane": "shopping"},
    {"url": "https://en.wikipedia.org/wiki/Watch", "title": "Watch",
     "description": "A timepiece worn on the wrist.", "text": "Rolex is a watch brand.",
     "lane": "culture"},
    {"url": "https://example.org/nasa", "title": "NASA missions",
     "description": "Space exploration and science.", "text": "NASA launches",
     "lane": "science"},
]


class SearchCoreTests(unittest.TestCase):
    def test_unicode_tokenization(self):
        self.assertEqual(tokenize("ＲＯＬＥＸ — Café"), ["rolex", "café"])

    def test_title_match_ranks_first(self):
        results = search(PAGES, "Rolex")
        self.assertEqual(results[0]["url"], "https://www.rolex.com/watches")

    def test_lane_filter(self):
        results = search(PAGES, "watch", lane="culture")
        self.assertEqual([r["lane"] for r in results], ["culture"])

    def test_empty_query(self):
        self.assertEqual(search(PAGES, "   "), [])

    def test_duplicate_urls_removed(self):
        results = search(PAGES + [PAGES[0]], "Rolex")
        self.assertEqual(sum(r["url"] == PAGES[0]["url"] for r in results), 1)

    def test_invalid_urls_excluded(self):
        results = search([{"url": "javascript:alert(1)", "title": "Rolex"}], "Rolex")
        self.assertEqual(results, [])

    def test_limit_is_bounded(self):
        self.assertLessEqual(len(search(PAGES, "watch", limit=9999)), 50)

    def test_snippet_is_returned(self):
        results = search(PAGES, "Rolex")
        self.assertTrue(results[0]["snippet"])

    def test_intelligence_evidence_is_attached(self):
        results = search(PAGES, "Rolex")
        self.assertIn("intelligence_score", results[0])
        self.assertIn("evidence_band", results[0])
        self.assertGreaterEqual(results[0]["intelligence_score"], 0)
        self.assertLessEqual(results[0]["intelligence_score"], 1)

    def test_independent_hosts_add_corroboration(self):
        pages = [
            {"url": "https://one.example/story", "title": "Apollo mission",
             "description": "Apollo moon landing", "fetched_at": 1794960000},
            {"url": "https://two.example/story", "title": "Apollo mission",
             "description": "Apollo moon landing", "fetched_at": 1794960000},
        ]
        results = search(pages, "Apollo")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["corroborating_hosts"], 1)
        self.assertGreater(results[0]["evidence_components"]["corroboration"], 0)


if __name__ == "__main__":
    unittest.main()
