import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("crawl.py")
SPEC = importlib.util.spec_from_file_location("everythinghub_crawl", MODULE_PATH)
crawl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crawl)

class CrawlerTests(unittest.TestCase):
    def test_clean_removes_tracking_and_fragment(self):
        self.assertEqual(
            crawl.clean("HTTPS://Example.com/a?utm_source=x&id=7#section"),
            "https://example.com/a?id=7",
        )

    def test_clean_rejects_non_web_urls(self):
        self.assertIsNone(crawl.clean("javascript:alert(1)"))
        self.assertIsNone(crawl.clean("mailto:test@example.com"))

    def test_parser_extracts_title_description_text_and_links(self):
        parser = crawl.PageParser()
        parser.feed(
            '<html><head><title>Example Page</title>'
            '<meta name="description" content="A useful page"></head>'
            '<body><nav>Navigation</nav><main>Hello searchable world'
            '<a href="/next">Next</a></main><script>secret()</script></body></html>'
        )
        self.assertEqual(" ".join(parser.title), "Example Page")
        self.assertEqual(parser.description, "A useful page")
        text = " ".join(parser.text)
        self.assertIn("Hello searchable world", text)
        self.assertNotIn("Navigation", text)
        self.assertNotIn("secret()", text)
        self.assertIn("/next", parser.links)

if __name__ == "__main__":
    unittest.main()
