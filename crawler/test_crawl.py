import importlib.util
import unittest
import urllib.robotparser
from collections import deque
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("crawl.py")
SPEC = importlib.util.spec_from_file_location("everythinghub_crawl", MODULE_PATH)
crawl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crawl)

class FakeRobotsResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


class FakePageHeaders:
    def get_all(self, _name):
        return []

    def get_content_type(self):
        return "text/html"


class FakePageResponse:
    def __init__(self, body, url="https://example.com/page"):
        self.body = body
        self.url = url
        self.headers = FakePageHeaders()
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]


class CrawlerTests(unittest.TestCase):
    def test_request_accepts_response_at_exact_byte_limit(self):
        response = FakePageResponse(b"1234")
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(crawl.urllib.request, "build_opener", return_value=opener):
            final_url, content_type, x_robots_tags, body = crawl.request(
                "https://example.com/page", lambda _url: True, max_bytes=4
            )
        self.assertEqual(final_url, "https://example.com/page")
        self.assertEqual(content_type, "text/html")
        self.assertEqual(x_robots_tags, [])
        self.assertEqual(body, b"1234")
        self.assertEqual(response.read_limit, 5)

    def test_request_rejects_oversized_response_instead_of_indexing_truncated_body(self):
        response = FakePageResponse(b"12345")
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(crawl.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "exceeds safe size limit"):
                crawl.request(
                    "https://example.com/page", lambda _url: True, max_bytes=4
                )
        self.assertEqual(response.read_limit, 5)

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

    def test_access_challenge_policy_excludes_short_privacy_stub_and_disables_following(self):
        directives = set()
        detected = crawl.apply_access_challenge_policy(
            "", "", "Powered and protected by Privacy", directives
        )
        self.assertTrue(detected)
        self.assertEqual(directives, {"noindex", "nofollow"})
        record = crawl.build_page_record(
            "https://example.com/challenge", "", "", "Powered and protected by Privacy",
            1, directives,
        )
        self.assertTrue(record["_eh_noindex"])
        self.assertEqual(record["depth"], 1)

    def test_access_challenge_policy_does_not_suppress_substantive_pages(self):
        directives = set()
        body = ("This article explains browser security and privacy. " * 40)
        self.assertFalse(crawl.apply_access_challenge_policy(
            "Browser Security Guide", "", body, directives
        ))
        self.assertEqual(directives, set())

    def test_parser_extracts_robots_meta_directives(self):
        parser = crawl.PageParser()
        parser.feed(
            '<meta name="robots" content="max-snippet:50, noindex, nofollow, nosnippet">'
            '<meta name="googlebot" content="index">'
        )
        self.assertEqual(
            parser.robots_directives,
            {"noindex", "nofollow", "nosnippet", "max-snippet:50"},
        )

    def test_parser_does_not_follow_individually_nofollow_links(self):
        parser = crawl.PageParser()
        parser.feed(
            '<a href="/follow">Follow</a>'
            '<a href="/skip" rel="nofollow">Skip</a>'
        )
        self.assertEqual(parser.links, ["/follow"])

    def test_x_robots_tag_applies_only_to_matching_crawler(self):
        self.assertEqual(
            crawl.parse_indexing_directives([
                "googlebot: noindex, nofollow",
                "EverythingHubCrawler: noindex",
            ]),
            {"noindex"},
        )

    def test_max_snippet_is_crawler_specific(self):
        self.assertEqual(
            crawl.parse_indexing_directives([
                "googlebot: max-snippet:10",
                "EverythingHubCrawler: max-snippet:7, nosnippet",
            ]),
            {"max-snippet:7", "nosnippet"},
        )

    def test_nosnippet_suppresses_text_and_description(self):
        self.assertEqual(
            crawl.apply_snippet_policy(
                "searchable body text", "a description", {"nosnippet"}
            ),
            ("", ""),
        )

    def test_max_snippet_truncates_text_and_description(self):
        self.assertEqual(
            crawl.apply_snippet_policy(
                "abcdefghij", "description", {"max-snippet:6", "max-snippet:4"}
            ),
            ("abcd", "desc"),
        )

    def test_max_snippet_zero_suppresses_text_and_description(self):
        self.assertEqual(
            crawl.apply_snippet_policy("body", "description", {"max-snippet:0"}),
            ("", ""),
        )

    def test_none_directive_implies_noindex_and_nofollow(self):
        self.assertEqual(
            crawl.parse_indexing_directives(["none"]),
            {"none", "noindex", "nofollow"},
        )

    def test_noindex_page_keeps_depth_for_following_allowed_links(self):
        page_url = "https://example.com/listing"
        next_url = "https://example.com/listing/next"
        record = crawl.build_page_record(
            page_url, "Private listing", "", "body", 1, {"noindex"}
        )
        self.assertTrue(record["_eh_noindex"])
        self.assertEqual(record["depth"], 1)
        self.assertNotIn("title", record)

        queue = deque()
        seen = {page_url}
        added = crawl.enqueue_discovered_links(queue, seen, [next_url], record)
        self.assertEqual(added, 1)
        self.assertEqual(list(queue), [(next_url, 2)])

    def test_noindex_page_with_nofollow_has_no_discoverable_links(self):
        record = crawl.build_page_record(
            "https://example.com/private", "Private", "", "", 2,
            {"noindex", "nofollow"},
        )
        self.assertEqual(record["depth"], 2)
        queue = deque()
        seen = {record["url"]}
        self.assertEqual(crawl.enqueue_discovered_links(queue, seen, [], record), 0)
        self.assertEqual(list(queue), [])

    def test_merge_index_records_removes_short_access_challenge_stubs(self):
        existing = [
            {
                "url": "https://example.com/challenge",
                "title": "",
                "description": "",
                "text": "Powered and protected by Privacy",
            },
            {
                "url": "https://example.com/keep",
                "title": "Useful page",
                "description": "",
                "text": "Substantive content that belongs in the search index.",
            },
        ]
        merged = crawl.merge_index_records(existing, {})
        self.assertEqual(set(merged), {"https://example.com/keep"})

    def test_merge_index_records_removes_only_explicit_noindex_urls(self):
        existing = [
            {"url": "https://example.com/noindex", "title": "Old private listing"},
            {"url": "https://example.com/keep", "title": "Keep this page"},
        ]
        new = {
            "https://example.com/new": {
                "url": "https://example.com/new", "title": "New page", "lane": "general"
            }
        }
        merged = crawl.merge_index_records(
            existing, new, {"https://example.com/noindex"}
        )
        self.assertEqual(
            set(merged),
            {"https://example.com/keep", "https://example.com/new"},
        )

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

    def test_robots_parser_reads_sitemap_declarations(self):
        parser = urllib.robotparser.RobotFileParser()
        parser.parse([
            "User-agent: *",
            "Allow: /",
            "Sitemap: https://example.com/sitemap.xml",
        ])
        self.assertEqual(parser.site_maps(), ["https://example.com/sitemap.xml"])


    def test_robots_policy_fetch_parses_allow_and_disallow_rules(self):
        body = b"User-agent: *\nAllow: /\nDisallow: /private\n"
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

    def test_robots_longest_match_and_allow_tie_break_are_order_independent(self):
        body = b"User-agent: *\nAllow: /\nDisallow: /private\n"
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

        tied = b"User-agent: *\nDisallow: /private\nAllow: /private\n"
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(tied)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

    def test_robots_wildcard_and_end_anchor_are_respected(self):
        body = b"User-agent: *\nDisallow: /private*\nAllow: /private/public$\n"
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/public/child"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/other"))

    def test_specific_user_agent_group_takes_precedence_over_wildcard(self):
        body = (
            b"User-agent: *\nDisallow: /\n\n"
            b"User-agent: EverythingHubCrawler\nDisallow: /private\n"
        )
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

    def test_repeated_matching_user_agent_groups_are_combined(self):
        body = (
            b"User-agent: EverythingHubCrawler\nDisallow: /private\n\n"
            b"User-agent: EverythingHubCrawler\nAllow: /private/public\n"
        )
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/other"))

    def test_robots_policy_fails_closed_on_http_error(self):
        error = crawl.urllib.error.HTTPError(
            "https://example.com/robots.txt", 404, "Not Found", {}, None
        )
        with mock.patch.object(crawl.urllib.request, "urlopen", side_effect=error):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(parser.disallow_all)
        self.assertEqual(parser.everythinghub_policy_error, "http_404")
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public"))

    def test_robots_policy_fails_closed_on_rate_limit_and_server_errors(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                error = crawl.urllib.error.HTTPError(
                    "https://example.com/robots.txt", status, "Unavailable", {}, None
                )
                with mock.patch.object(crawl.urllib.request, "urlopen", side_effect=error):
                    parser = crawl.load_robots_policy("https://example.com")
                self.assertTrue(parser.disallow_all)
                self.assertEqual(parser.everythinghub_policy_error, f"http_{status}")
                self.assertFalse(
                    crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public")
                )

    def test_robots_policy_fails_closed_on_network_timeout(self):
        with mock.patch.object(
            crawl.urllib.request, "urlopen", side_effect=TimeoutError("robots timeout")
        ):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(parser.disallow_all)
        self.assertEqual(parser.everythinghub_policy_error, "unavailable")
        self.assertFalse(
            crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public")
        )

    def test_robots_policy_fails_closed_on_oversized_file(self):
        body = b"#" * (crawl.ROBOTS_MAX_BYTES + 1)
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(parser.disallow_all)
        self.assertEqual(parser.everythinghub_policy_error, "too_large")
        self.assertFalse(parser.can_fetch(crawl.UA, "https://example.com/public"))

    def test_sitemap_urlset_extracts_bounded_same_host_pages(self):
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/one?utm_source=test</loc></url>
          <url><loc>https://example.com/two</loc></url>
          <url><loc>https://example.com/three</loc></url>
          <url><loc>https://other.example/skip</loc></url>
        </urlset>"""
        pages, children = crawl.parse_sitemap_xml(
            body, "https://example.com/sitemap.xml", max_urls=2
        )
        self.assertEqual(pages, ["https://example.com/one", "https://example.com/two"])
        self.assertEqual(children, [])

    def test_sitemap_index_returns_only_same_host_child_sitemaps(self):
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.com/part-1.xml</loc></sitemap>
          <sitemap><loc>https://other.example/part-2.xml</loc></sitemap>
        </sitemapindex>"""
        pages, children = crawl.parse_sitemap_xml(
            body, "https://example.com/sitemap-index.xml"
        )
        self.assertEqual(pages, [])
        self.assertEqual(children, ["https://example.com/part-1.xml"])

    def test_sitemap_parser_fails_closed_on_malformed_xml(self):
        pages, children = crawl.parse_sitemap_xml(
            b"not XML", "https://example.com/sitemap.xml"
        )
        self.assertEqual(pages, [])
        self.assertEqual(children, [])

    def test_retry_after_seconds_parses_numeric_header(self):
        class FakeError:
            headers = {"Retry-After": "120"}
        self.assertEqual(crawl.retry_after_seconds(FakeError(), 30), 120.0)

    def test_retry_after_seconds_uses_default_for_invalid_header(self):
        class FakeError:
            headers = {"Retry-After": "later"}
        self.assertEqual(crawl.retry_after_seconds(FakeError(), 45), 45.0)

    def test_retry_after_seconds_caps_long_server_delays(self):
        class FakeError:
            headers = {"Retry-After": str(crawl.MAX_RETRY_AFTER_SECONDS * 2)}
        self.assertEqual(
            crawl.retry_after_seconds(FakeError()),
            float(crawl.MAX_RETRY_AFTER_SECONDS),
        )

    def test_select_batch_respects_remaining_worker_limit(self):
        queue = deque([
            ("https://one.example/a", 0),
            ("https://two.example/b", 0),
            ("https://three.example/c", 0),
        ])
        batch = crawl.select_batch(queue, lambda _url: True, {}, now=100.0, workers=2)
        self.assertEqual(len(batch), 2)
        self.assertEqual(len(queue), 1)

    def test_effective_host_delay_respects_robots_directives(self):
        parser = urllib.robotparser.RobotFileParser()
        parser.parse([
            "User-agent: *",
            "Crawl-delay: 4",
            "Request-rate: 2/3",
        ])
        self.assertEqual(crawl.effective_host_delay(parser), 4.0)

    def test_effective_host_delay_combines_all_matching_groups(self):
        body = (
            b"User-agent: *\nCrawl-delay: 1\nRequest-rate: 10/1\n\n"
            b"User-agent: EverythingHubCrawler\nCrawl-delay: 4\nRequest-rate: 1/6\n\n"
            b"User-agent: EverythingHubCrawler\nCrawl-delay: 7\nRequest-rate: 2/3\n"
        )
        with mock.patch.object(crawl.urllib.request, "urlopen", return_value=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertEqual(crawl.effective_host_delay(parser), 7.0)

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

    def test_default_batch_uses_sixteen_distinct_hosts(self):
        queue = deque([
            (f"https://host-{index}.example/page", 0)
            for index in range(19)
        ])
        # HTTP and HTTPS for one hostname must still count as the same host.
        queue.append(("http://host-0.example/second", 0))

        batch = crawl.select_batch(
            queue, lambda _url: True, {}, now=100.0
        )

        self.assertEqual(crawl.WORKERS, 16)
        self.assertEqual(len(batch), 16)
        self.assertEqual(
            len({crawl.host_key(url) for url, _depth in batch}),
            len(batch),
        )
        self.assertEqual(len(queue), 4)
        self.assertIn(("http://host-0.example/second", 0), list(queue))

if __name__ == "__main__":
    unittest.main()
