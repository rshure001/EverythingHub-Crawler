import importlib.util
import unittest
import urllib.robotparser
from collections import deque
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

    def test_page_redirect_handler_blocks_cross_host_redirects(self):
        handler = crawl.RobotsAwareRedirectHandler(lambda _url: True)
        request = crawl.urllib.request.Request("https://example.com/start")
        with self.assertRaises(crawl.urllib.error.HTTPError):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://other.example/target"
            )

    def test_page_redirect_handler_checks_robots_before_same_host_scheme_redirect(self):
        handler = crawl.RobotsAwareRedirectHandler(lambda _url: True)
        request = crawl.urllib.request.Request("http://example.com/start")
        redirected = handler.redirect_request(
            request, None, 301, "Moved", {}, "https://example.com/next"
        )
        self.assertEqual(redirected.full_url, "https://example.com/next")

    def test_page_redirect_handler_blocks_same_host_redirect_disallowed_by_robots(self):
        handler = crawl.RobotsAwareRedirectHandler(lambda _url: False)
        request = crawl.urllib.request.Request("http://example.com/start")
        with self.assertRaises(crawl.urllib.error.HTTPError):
            handler.redirect_request(
                request, None, 301, "Moved", {}, "https://example.com/private"
            )

    def test_effective_host_delay_respects_robots_directives(self):
        parser = urllib.robotparser.RobotFileParser()
        parser.parse([
            "User-agent: *",
            "Crawl-delay: 4",
            "Request-rate: 2/3",
        ])
        self.assertEqual(crawl.effective_host_delay(parser), 4.0)

    def test_effective_host_delay_enforces_minimum_interval(self):
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(["User-agent: *"])
        self.assertEqual(crawl.effective_host_delay(parser), crawl.HOST_MIN_INTERVAL)

    def test_host_key_normalizes_http_and_https(self):
        self.assertEqual(crawl.host_key("http://example.com/one"), "example.com")
        self.assertEqual(crawl.host_key("https://example.com/two"), "example.com")

    def test_select_batch_limits_each_host_to_one_request_across_schemes(self):
        queue = deque([
            ("http://example.com/one", 0),
            ("https://example.com/two", 0),
            ("https://other.example/three", 0),
        ])
        batch = crawl.select_batch(queue, lambda _url: True, {}, now=100.0, workers=8)
        self.assertEqual(
            {crawl.host_key(url) for url, _depth in batch},
            {"example.com", "other.example"},
        )
        self.assertEqual(list(queue), [("https://example.com/two", 0)])

    def test_select_batch_defers_hosts_until_their_rate_window_opens(self):
        queue = deque([
            ("https://slow.example/one", 0),
            ("https://ready.example/two", 0),
        ])
        next_allowed = {"slow.example": 130.0}
        batch = crawl.select_batch(queue, lambda _url: True, next_allowed, now=100.0)
        self.assertEqual(batch, [("https://ready.example/two", 0)])
        self.assertEqual(list(queue), [("https://slow.example/one", 0)])

if __name__ == "__main__":
    unittest.main()
