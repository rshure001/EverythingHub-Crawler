import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("static_search_api.py")
SPEC = importlib.util.spec_from_file_location("eh_static_search", MODULE_PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class StaticSearchTests(unittest.TestCase):
    def setUp(self):
        pages = [
            {"url":"https://nasa.gov/moon","title":"NASA Apollo Moon Mission",
             "description":"Space exploration mission","text":"NASA Apollo lunar mission",
             "lane":"science"},
            {"url":"https://example.com/rolex","title":"Rolex Watches",
             "description":"Luxury watch information","text":"Rolex watch models",
             "lane":"business"},
            {"url":"https://example.com/nasa-news","title":"NASA latest news",
             "description":"NASA agency update","text":"NASA space science",
             "lane":"news"},
        ]
        api.DOCS = []
        from collections import Counter
        import re
        for page in pages:
            tok = lambda value: Counter(re.findall(r"[a-z0-9]{2,}", value.lower()))
            api.DOCS.append((page, tok(page["title"]), tok(page["description"]),
                             tok(page["text"]), tok(page["url"])))

    def test_title_hits_rank_first(self):
        result = api.search("Apollo", "", 10, 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["url"], "https://nasa.gov/moon")

    def test_lane_filter(self):
        result = api.search("NASA", "news", 10, 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["tentacle"], "news")

    def test_pagination(self):
        result = api.search("NASA", "", 1, 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertFalse(result["hasMore"])

    def test_no_match(self):
        result = api.search("nonexistentterm", "", 10, 0)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])

    def test_query_terms_match_without_case_sensitivity(self):
        result = api.search("nAsA", "", 10, 0)
        self.assertEqual(result["total"], 2)


if __name__ == "__main__":
    unittest.main()
