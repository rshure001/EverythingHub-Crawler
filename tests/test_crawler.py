import unittest

from crawler.crawl import PageParser, clean


class UrlNormalizationTests(unittest.TestCase):
    def test_removes_fragment_and_tracking_parameters(self):
        self.assertEqual(
            clean("HTTPS://Example.COM/a?utm_source=newsletter&keep=1#section"),
            "https://example.com/a?keep=1",
        )

    def test_preserves_meaningful_query_parameters(self):
        self.assertEqual(
            clean("https://example.com/search?q=dog+tags&page=2"),
            "https://example.com/search?q=dog+tags&page=2",
        )

    def test_rejects_non_http_schemes_and_relative_urls(self):
        self.assertIsNone(clean("javascript:alert(1)"))
        self.assertIsNone(clean("/relative/path"))


class HtmlExtractionTests(unittest.TestCase):
    def test_extracts_title_description_text_and_links(self):
        parser = PageParser()
        parser.feed("""
            <html><head>
              <title>Example Page</title>
              <meta name="description" content="A useful description">
            </head><body>
              <nav>Repeated navigation</nav>
              <main><p>Useful page content</p>
              <a href="/next">Next page</a></main>
              <script>ignoreThis()</script>
            </body></html>
        """)

        self.assertEqual(" ".join(parser.title).strip(), "Example Page")
        self.assertEqual(parser.description, "A useful description")
        self.assertIn("Useful page content", " ".join(parser.text))
        self.assertNotIn("Repeated navigation", " ".join(parser.text))
        self.assertNotIn("ignoreThis", " ".join(parser.text))
        self.assertIn("/next", parser.links)


if __name__ == "__main__":
    unittest.main()
