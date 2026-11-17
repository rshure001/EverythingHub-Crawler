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


if __name__ == "__main__":
    unittest.main()
