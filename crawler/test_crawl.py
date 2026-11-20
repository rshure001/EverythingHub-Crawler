import importlib.util
from contextlib import contextmanager
import unittest
import tempfile
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


@contextmanager
def patched_pinned_opener(response=None, side_effect=None):
    opener = mock.Mock()
    if side_effect is not None:
        opener.open.side_effect = side_effect
    else:
        opener.open.return_value = response
    with mock.patch.object(crawl, "build_pinned_opener", return_value=opener):
        yield opener


class CrawlerTests(unittest.TestCase):
    def test_normalize_text_removes_nul_and_collapses_whitespace(self):
        self.assertEqual(crawl.normalize_text(" Alpha\x00Beta\n Gamma ", 100), "AlphaBeta Gamma")

    def test_media_discovery_removes_nul_from_alt_text(self):
        candidates = crawl.discover_media('<img src="/image.png" alt="A\x00B">', "https://example.com/")
        self.assertEqual(candidates[0]["alt_text"], "AB")
    def test_summarize_fetch_outcomes_separates_http_html_and_failures(self):
        outcomes = [
            {"http_success": True, "html_parsed": True, "non_html": False, "failed": False},
            {"http_success": True, "html_parsed": False, "non_html": True, "failed": False},
            {"http_success": False, "html_parsed": False, "non_html": False, "failed": True},
            {"http_success": True, "html_parsed": False, "non_html": False, "failed": True},
        ]
        self.assertEqual(
            crawl.summarize_fetch_outcomes(outcomes),
            {
                "fetch_attempts": 4,
                "successful_http_responses": 3,
                "html_pages_parsed": 1,
                "non_html_responses": 1,
                "fetch_or_parse_errors": 2,
            },
        )

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

    def test_clean_skips_non_html_assets_but_keeps_xml_sitemaps(self):
        for url in (
            "https://example.com/file.pdf",
            "https://example.com/image.JPG",
            "https://example.com/video.mp4",
            "https://example.com/site.css",
            "https://example.com/font.woff2",
        ):
            with self.subTest(url=url):
                self.assertIsNone(crawl.clean(url))
        self.assertEqual(
            crawl.clean("https://example.com/sitemap.xml"),
            "https://example.com/sitemap.xml",
        )

    def test_sitemap_history_resets_once_for_new_discovery_version(self):
        previous = {"sitemap_checked": {"old.example": 123}}
        self.assertEqual(crawl.restore_sitemap_checked(previous), {})
        current = {
            "sitemap_discovery_version": crawl.SITEMAP_DISCOVERY_VERSION,
            "sitemap_checked": {"current.example": 456},
        }
        self.assertEqual(
            crawl.restore_sitemap_checked(current),
            {"current.example": 456},
        )

    def test_robots_preload_fetches_each_distinct_site_once(self):
        calls = []
        result = crawl.preload_robots_policies(
            ["https://one.example", "https://two.example", "https://one.example"],
            loader=lambda site: calls.append(site) or f"policy:{site}",
            workers=2,
        )
        self.assertEqual(set(calls), {
            "https://one.example", "https://two.example",
        })
        self.assertEqual(result, {
            "https://one.example": "policy:https://one.example",
            "https://two.example": "policy:https://two.example",
        })

    def test_frontier_restore_discards_legacy_non_html_and_bad_urls(self):
        restored = crawl.restore_frontier_queue([
            {"url": "https://example.com/article", "depth": 1},
            {"url": "https://example.com/old.pdf", "depth": 2},
            {"url": "https://example.com/image.png", "depth": 1},
            {"url": "ftp://example.com/file", "depth": 1},
            {"url": "https://example.com/too-deep", "depth": 4},
            {"url": "https://example.com/bad-depth", "depth": "bad"},
            {"url": "https://example.com/second", "depth": 0},
        ])
        self.assertEqual(list(restored), [
            ("https://example.com/article", 1),
            ("https://example.com/second", 0),
        ])

    def test_clean_rejects_non_web_and_credential_bearing_urls(self):
        self.assertIsNone(crawl.clean("javascript:alert(1)"))
        self.assertIsNone(crawl.clean("mailto:test@example.com"))
        self.assertIsNone(crawl.clean("https://user:secret@example.com/private"))
        self.assertIsNone(crawl.clean("https://@example.com/private"))
        self.assertIsNone(crawl.clean("https://[invalid-ipv6"))

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

    def test_parser_extracts_publication_date_and_prefers_specific_article_metadata(self):
        parser = crawl.PageParser()
        parser.feed(
            '<meta name="date" content="2020-01-01">'
            '<meta property="article:published_time" content="2024-05-01T12:30:00-04:00">'
            '<meta property="article:modified_time" content="2024-06-01T00:00:00Z">'
        )
        self.assertEqual(parser.published_at, "2024-05-01T16:30:00Z")

    def test_parser_extracts_itemprop_and_rfc_publication_dates(self):
        parser = crawl.PageParser()
        parser.feed('<meta itemprop="datePublished" content="Wed, 01 May 2024 12:30:00 -0400">')
        self.assertEqual(parser.published_at, "2024-05-01T16:30:00Z")

    def test_parser_extracts_itemprop_time_element_publication_date(self):
        parser = crawl.PageParser()
        parser.feed('<time itemprop="datePublished" datetime="2024-05-01T12:30:00-04:00">May 1</time>')
        self.assertEqual(parser.published_at, "2024-05-01T16:30:00Z")

    def test_specific_time_itemprop_overrides_generic_date_meta(self):
        parser = crawl.PageParser()
        parser.feed(
            '<meta name="date" content="2020-01-01">'
            '<time itemprop="datePublished" datetime="2024-05-01T12:30:00-04:00">May 1</time>'
        )
        self.assertEqual(parser.published_at, "2024-05-01T16:30:00Z")

    def test_parse_published_at_rejects_unparseable_values(self):
        self.assertIsNone(crawl.parse_published_at("not-a-date"))
        self.assertIsNone(crawl.parse_published_at(""))

    def test_build_page_record_preserves_only_valid_publication_dates(self):
        record = crawl.build_page_record(
            "https://example.com/story", "Story", "", "Useful content", 1, set(),
            "2024-05-01T12:30:00-04:00",
        )
        self.assertEqual(record["published_at"], "2024-05-01T16:30:00Z")
        invalid = crawl.build_page_record(
            "https://example.com/unknown", "Unknown", "", "Useful content", 1, set(),
            "not-a-date",
        )
        self.assertNotIn("published_at", invalid)
        noindex = crawl.build_page_record(
            "https://example.com/noindex", "Hidden", "", "", 1, {"noindex"},
            "2024-05-01T12:30:00Z",
        )
        self.assertTrue(noindex["_eh_noindex"])
        self.assertNotIn("published_at", noindex)

    def test_build_page_record_uses_readable_url_title_when_source_title_is_blank(self):
        record = crawl.build_page_record(
            "https://example.com/resources/veteran-resources.html",
            "   ", "", "Useful resource content", 1, set(),
        )
        self.assertEqual(record["title"], "veteran resources")

    def test_merge_index_records_preserves_publication_date_when_recrawl_omits_it(self):
        existing = [{
            "url": "https://example.com/story",
            "title": "Old title",
            "published_at": "2024-05-01T16:30:00Z",
        }]
        new_records = {
            "https://example.com/story": {
                "url": "https://example.com/story",
                "title": "Updated title",
                "fetched_at": 1794960000,
            }
        }
        merged = crawl.merge_index_records(existing, new_records)
        self.assertEqual(merged["https://example.com/story"]["title"], "Updated title")
        self.assertEqual(
            merged["https://example.com/story"]["published_at"],
            "2024-05-01T16:30:00Z",
        )

    def test_merge_index_records_replaces_publication_date_when_new_one_is_available(self):
        existing = [{
            "url": "https://example.com/story",
            "published_at": "2024-05-01T16:30:00Z",
        }]
        new_records = {
            "https://example.com/story": {
                "url": "https://example.com/story",
                "title": "Updated title",
                "published_at": "2025-06-02T10:00:00Z",
            }
        }
        merged = crawl.merge_index_records(existing, new_records)
        self.assertEqual(
            merged["https://example.com/story"]["published_at"],
            "2025-06-02T10:00:00Z",
        )

    def test_merge_index_records_backfills_legacy_blank_titles_without_dropping_pages(self):
        existing = [{
            "url": "https://example.com/about-us",
            "title": "",
            "description": "",
            "text": "A substantive page that should remain in the public index.",
        }]
        merged = crawl.merge_index_records(existing, {})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged["https://example.com/about-us"]["title"], "about us")

    def test_fallback_page_title_uses_host_for_root_pages(self):
        self.assertEqual(
            crawl.fallback_page_title("https://example.com/"),
            "example.com",
        )

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

    def test_discovery_queue_limit_uses_pending_queue_not_seen_history(self):
        queue = deque()
        seen = {"https://example.com/old-indexed"}
        record = {"url": "https://example.com/page", "depth": 0}
        added = crawl.enqueue_discovered_links(
            queue,
            seen,
            [
                "https://example.com/first",
                "https://example.com/second",
            ],
            record,
            queue_limit=1,
        )
        self.assertEqual(added, 1)
        self.assertEqual(list(queue), [("https://example.com/first", 1)])
        self.assertNotIn("https://example.com/second", seen)

    def test_discovery_skips_urls_already_in_canonical_index(self):
        queue = deque()
        seen = set()
        indexed = {"https://example.com/already-indexed"}
        record = {"url": "https://example.com/page", "depth": 0}
        added = crawl.enqueue_discovered_links(
            queue,
            seen,
            [
                "https://example.com/already-indexed",
                "https://example.com/new",
            ],
            record,
            indexed_urls=indexed,
        )
        self.assertEqual(added, 1)
        self.assertEqual(list(queue), [("https://example.com/new", 1)])

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

    def test_page_redirect_handler_checks_robots_before_same_origin_redirect(self):
        handler = crawl.RobotsAwareRedirectHandler(lambda _url: True)
        request = crawl.urllib.request.Request("https://example.com/start")
        redirected = handler.redirect_request(
            request, None, 301, "Moved", {}, "https://example.com/next"
        )
        self.assertEqual(redirected.full_url, "https://example.com/next")

    def test_page_redirect_handler_rejects_same_host_scheme_or_port_change(self):
        handler = crawl.RobotsAwareRedirectHandler(lambda _url: True)
        request = crawl.urllib.request.Request("https://example.com/start")
        for target in ("http://example.com/downgrade", "https://example.com:8443/other-port"):
            with self.subTest(target=target), self.assertRaises(crawl.urllib.error.HTTPError):
                handler.redirect_request(
                    request, None, 302, "Found", {}, target
                )

    def test_page_redirect_handler_blocks_same_origin_redirect_disallowed_by_robots(self):
        handler = crawl.RobotsAwareRedirectHandler(lambda _url: False)
        request = crawl.urllib.request.Request("https://example.com/start")
        with self.assertRaises(crawl.urllib.error.HTTPError):
            handler.redirect_request(
                request, None, 301, "Moved", {}, "https://example.com/private"
            )

    def test_robots_file_redirect_handler_allows_cross_authority_https_redirect(self):
        handler = crawl.RobotsFileRedirectHandler()
        request = crawl.urllib.request.Request("https://example.com/robots.txt")
        redirected = handler.redirect_request(
            request, None, 301, "Moved", {}, "https://cdn.example.net/robots.txt"
        )
        self.assertEqual(redirected.full_url, "https://cdn.example.net/robots.txt")
        self.assertEqual(handler.max_redirections, 5)
        self.assertEqual(handler.max_repeats, 5)

    def test_robots_file_redirect_handler_rejects_downgrade_and_unsafe_targets(self):
        handler = crawl.RobotsFileRedirectHandler()
        request = crawl.urllib.request.Request("https://example.com/robots.txt")
        for target in (
            "http://cdn.example.net/robots.txt",
            "ftp://cdn.example.net/robots.txt",
            "https://user:pass@cdn.example.net/robots.txt",
        ):
            with self.subTest(target=target), self.assertRaises(crawl.urllib.error.HTTPError):
                handler.redirect_request(
                    request, None, 302, "Found", {}, target
                )

    def test_robots_policy_uses_bounded_cross_authority_redirect_handler(self):
        body = b"User-agent: *\nAllow: /\nDisallow: /private\n"
        opener = mock.Mock()
        opener.open.return_value = FakeRobotsResponse(body)
        with mock.patch.object(crawl, "build_pinned_opener", return_value=opener) as builder:
            parser = crawl.load_robots_policy("https://example.com")
        handler = builder.call_args.args[0]
        self.assertIsInstance(handler, crawl.RobotsFileRedirectHandler)
        self.assertEqual(handler.max_redirections, 5)
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

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
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

    def test_robots_longest_match_and_allow_tie_break_are_order_independent(self):
        body = b"User-agent: *\nAllow: /\nDisallow: /private\n"
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

        tied = b"User-agent: *\nDisallow: /private\nAllow: /private\n"
        with patched_pinned_opener(response=FakeRobotsResponse(tied)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

    def test_robots_wildcard_and_end_anchor_are_respected(self):
        body = b"User-agent: *\nDisallow: /private*\nAllow: /private/public$\n"
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/public/child"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/other"))

    def test_specific_user_agent_group_takes_precedence_over_wildcard(self):
        body = (
            b"User-agent: *\nDisallow: /\n\n"
            b"User-agent: EverythingHubCrawler\nDisallow: /private\n"
        )
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private"))

    def test_repeated_matching_user_agent_groups_are_combined(self):
        body = (
            b"User-agent: EverythingHubCrawler\nDisallow: /private\n\n"
            b"User-agent: EverythingHubCrawler\nAllow: /private/public\n"
        )
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/public"))
        self.assertFalse(crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/private/other"))

    def test_robots_policy_fails_closed_on_http_error(self):
        error = crawl.urllib.error.HTTPError(
            "https://example.com/robots.txt", 404, "Not Found", {}, None
        )
        with patched_pinned_opener(side_effect=error):
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
                with patched_pinned_opener(side_effect=error):
                    parser = crawl.load_robots_policy("https://example.com")
                self.assertTrue(parser.disallow_all)
                self.assertEqual(parser.everythinghub_policy_error, f"http_{status}")
                self.assertFalse(
                    crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public")
                )

    def test_robots_policy_fails_closed_on_network_timeout(self):
        with patched_pinned_opener(side_effect=TimeoutError("robots timeout")):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(parser.disallow_all)
        self.assertEqual(parser.everythinghub_policy_error, "unavailable")
        self.assertFalse(
            crawl.robots_can_fetch(parser, crawl.UA, "https://example.com/public")
        )

    def test_robots_policy_fails_closed_on_oversized_file(self):
        body = b"#" * (crawl.ROBOTS_MAX_BYTES + 1)
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(parser.disallow_all)
        self.assertEqual(parser.everythinghub_policy_error, "too_large")
        self.assertFalse(parser.can_fetch(crawl.UA, "https://example.com/public"))

    def test_robots_policy_fails_closed_on_dns_pinning_error(self):
        with patched_pinned_opener(side_effect=crawl.UnsafeTargetError("private DNS target")):
            parser = crawl.load_robots_policy("https://example.com")
        self.assertTrue(parser.disallow_all)
        self.assertEqual(parser.everythinghub_policy_error, "unavailable")

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

    def test_retry_after_seconds_parses_http_date_and_clamps_past_dates(self):
        class FutureError:
            headers = {"Retry-After": "Tue, 17 Nov 2026 12:01:30 GMT"}

        class PastError:
            headers = {"Retry-After": "Tue, 17 Nov 2026 11:59:30 GMT"}

        fixed_now = crawl.datetime(2026, 11, 17, 12, 0, 0, tzinfo=crawl.timezone.utc)
        with mock.patch.object(crawl, "datetime") as datetime_mock:
            datetime_mock.now.return_value = fixed_now
            self.assertEqual(crawl.retry_after_seconds(FutureError(), 30), 90.0)
            self.assertEqual(crawl.retry_after_seconds(PastError(), 30), 0.0)

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

    def test_select_batch_preserves_stable_order_within_priority_depth_groups(self):
        first = "https://priority-a.example/first"
        second = "https://priority-b.example/second"
        same_host_later = "https://priority-a.example/later"
        third = "https://priority-c.example/third"
        shallow = "https://priority-d.example/shallow"
        regular = "https://regular.example/page"
        queue = deque([
            (first, 3),
            (second, 3),
            (same_host_later, 3),
            (third, 3),
            (shallow, 0),
            (regular, 3),
        ])

        batch = crawl.select_batch(
            queue, lambda _url: True, {}, now=100.0, workers=3,
            priority_urls={first, second, same_host_later, third, shallow},
        )

        self.assertEqual(batch, [(first, 3), (second, 3), (third, 3)])
        self.assertEqual(
            list(queue),
            [(same_host_later, 3), (shallow, 0), (regular, 3)],
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

    def test_select_batch_skips_hosts_with_requests_already_in_flight(self):
        queue = deque([
            ("https://busy.example/next", 0),
            ("https://ready.example/page", 1),
        ])

        batch = crawl.select_batch(
            queue, lambda _url: True, {}, now=100.0, workers=1,
            in_flight_hosts={"busy.example"},
        )

        self.assertEqual(batch, [("https://ready.example/page", 1)])
        self.assertEqual(list(queue), [("https://busy.example/next", 0)])

    def test_select_batch_prioritizes_seed_urls_ahead_of_deeper_frontier(self):
        seed = "https://www.britannica.com/"
        queue = deque([
            ("https://deep.example/page", 3),
            (seed, 0),
            ("https://middle.example/page", 2),
        ])

        batch = crawl.select_batch(
            queue, lambda _url: True, {}, now=100.0, workers=2,
            priority_urls={seed},
        )

        self.assertEqual(batch, [(seed, 0), ("https://deep.example/page", 3)])
        self.assertEqual(list(queue), [("https://middle.example/page", 2)])

    def test_select_batch_priority_does_not_bypass_host_rate_limit(self):
        seed = "https://www.noaa.gov/"
        queue = deque([
            (seed, 0),
            ("https://ready.example/page", 2),
        ])

        batch = crawl.select_batch(
            queue, lambda _url: True, {"www.noaa.gov": 200.0},
            now=100.0, workers=2, priority_urls={seed},
        )

        self.assertEqual(batch, [("https://ready.example/page", 2)])
        self.assertEqual(list(queue), [(seed, 0)])

    def test_select_batch_retains_depth_priority_when_no_seed_priority_is_given(self):
        queue = deque([
            ("https://shallow.example/page", 1),
            ("https://deep.example/page", 3),
        ])

        batch = crawl.select_batch(queue, lambda _url: True, {}, now=100.0, workers=2)

        self.assertEqual(
            batch,
            [("https://deep.example/page", 3), ("https://shallow.example/page", 1)],
        )

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
        with patched_pinned_opener(response=FakeRobotsResponse(body)):
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

    def test_default_batch_uses_two_hundred_fifty_distinct_hosts(self):
        queue = deque([
            (f"https://host-{index}.example/page", 0)
            for index in range(250)
        ])
        # HTTP and HTTPS for one hostname must still count as the same host.
        queue.append(("http://host-0.example/second", 0))

        batch = crawl.select_batch(
            queue, lambda _url: True, {}, now=100.0
        )

        self.assertEqual(crawl.WORKERS, 250)
        self.assertEqual(crawl.MAX_FETCH_PER_RUN, 12000)
        self.assertEqual(crawl.QUEUE_LIMIT, 100000)
        self.assertEqual(len(batch), 250)
        self.assertEqual(
            len({crawl.host_key(url) for url, _depth in batch}),
            len(batch),
        )
        self.assertEqual(len(queue), 1)
        self.assertIn(("http://host-0.example/second", 0), list(queue))

    def test_crawl_runtime_budget_preserves_postprocessing_headroom(self):
        self.assertEqual(crawl.MAX_RUN_SECONDS, 420)
        self.assertLessEqual(crawl.MAX_RUN_SECONDS + 140, 600)

    def test_save_json_writes_compact_semantically_identical_json(self):
        payload = {
            "updated_at": 123,
            "pages": [
                {"url": "https://example.com/", "title": "Café", "text": "A representative excerpt."},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            crawl.save_json(path, payload)
            raw = path.read_text(encoding="utf-8")
            self.assertEqual(crawl.load_json(path, {}), payload)
            self.assertEqual(
                raw,
                crawl.json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            )
            pretty = crawl.json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            self.assertLess(len(raw), len(pretty))
if __name__ == "__main__":
    unittest.main()
