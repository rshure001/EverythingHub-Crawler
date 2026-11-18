import email.message
import importlib.util
import os
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("eh_quad_crawl_worker", HERE / "crawl_worker.py")
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


class EhQuadCrawlerSafetyTests(unittest.TestCase):
    def test_schema_is_locked_to_eh_quad(self):
        self.assertEqual(WORKER.validate_schema("eh_quad"), "eh_quad")
        for value in ("public", "auth", "storage", "eh_quad,public", "eh_quad;drop schema public"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WORKER.validate_schema(value)

    def test_url_validation_rejects_credentials_bad_ports_and_non_http(self):
        rejected = (
            "https://user:secret@example.org/private",
            "https://@example.org/private",
            "https://example.org:invalid/path",
            "https://example.org:99999/path",
            "https://[2001:db8::1/path",
            "https://example.org/with space",
            "ftp://example.org/resource",
            " file:///tmp/private",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(WORKER.clean_url(url))

    def test_url_normalization_removes_tracking_and_fragments(self):
        self.assertEqual(
            WORKER.clean_url("HTTPS://Example.org/a?utm_source=x&keep=1#section"),
            "https://example.org/a?keep=1",
        )

    def test_private_targets_rejected_and_loopback_allowed_only_in_test_mode(self):
        self.assertFalse(WORKER.is_safe_target("http://127.0.0.1:8080/test", False))
        self.assertTrue(WORKER.is_safe_target("http://127.0.0.1:8080/test", True))
        self.assertFalse(WORKER.is_safe_target("http://10.0.0.1/test", True))
        self.assertTrue(WORKER.is_safe_target("https://8.8.8.8/test", False))

    def test_dns_private_address_fails_closed(self):
        fake = [(None, None, None, None, ("10.20.30.40", 443))]
        with mock.patch.object(WORKER.socket, "getaddrinfo", return_value=fake):
            self.assertFalse(WORKER.is_safe_target("https://example.org/path"))

    def test_robots_404_allows_but_503_and_429_fail_closed(self):
        parser = WORKER.parse_robots_policy(404, b"")
        self.assertTrue(WORKER.public_crawler.robots_can_fetch(parser, WORKER.USER_AGENT, "https://example.org/any"))
        with self.assertRaises(WORKER.RobotsUnavailable):
            WORKER.parse_robots_policy(503, b"temporary outage")
        with self.assertRaises(WORKER.RobotsUnavailable):
            WORKER.parse_robots_policy(429, b"rate limited")

    def test_worker_uses_public_crawler_longest_match_and_host_rate_policy(self):
        parser = WORKER.parse_robots_policy(
            200,
            b"User-agent: *\nAllow: /\nDisallow: /private\nAllow: /private/public\n"
            b"Crawl-delay: 3\nRequest-rate: 2/1\n",
        )
        can_fetch = WORKER.public_crawler.robots_can_fetch
        self.assertFalse(can_fetch(parser, WORKER.USER_AGENT, "https://example.org/private"))
        self.assertTrue(can_fetch(parser, WORKER.USER_AGENT, "https://example.org/private/public"))
        self.assertGreaterEqual(WORKER.public_crawler.effective_host_delay(parser), 3.0)

    def test_media_parser_honors_meta_robots_nofollow_and_media(self):
        parser = WORKER.MediaPageParser()
        parser.feed(
            '<html><head><title>Fixture</title>'
            '<meta name="robots" content="noindex, nofollow">'
            '<meta name="description" content="A fixture"></head>'
            '<body>Visible text <a href="/ok">ok</a>'
            '<a href="/skip" rel="nofollow">skip</a>'
            '<img src="/image.png" alt="image">'
            '<video src="/movie.mp4"></video></body></html>'
        )
        self.assertIn("noindex", parser.directives)
        self.assertIn("nofollow", parser.directives)
        self.assertEqual(parser.links, ["/ok"])
        self.assertEqual(len(parser.media), 2)
        self.assertEqual(parser.media[0][1], "image")
        self.assertEqual(parser.media[1][1], "video")

    def test_same_origin_redirect_allowed_cross_origin_rejected(self):
        handler = WORKER.SameOriginRobotsRedirectHandler(lambda _url: True)
        request = urllib.request.Request("https://example.org/start")
        headers = email.message.Message()
        self.assertIsNotNone(
            handler.redirect_request(request, None, 302, "Found", headers, "https://example.org/next")
        )
        with self.assertRaises(WORKER.CrossOriginRedirectError):
            handler.redirect_request(
                request, None, 302, "Found", headers, "https://other.example/next"
            )

    def test_same_origin_redirect_is_rejected_when_robots_disallows_target(self):
        handler = WORKER.SameOriginRobotsRedirectHandler(lambda _url: False)
        request = urllib.request.Request("https://example.org/start")
        with self.assertRaises(WORKER.RobotsDeniedRedirectError):
            handler.redirect_request(
                request, None, 302, "Found", email.message.Message(), "https://example.org/private"
            )

    def test_retry_after_seconds_and_http_date_are_honored_and_bounded(self):
        seconds_error = urllib.error.HTTPError(
            "https://example.org/", 503, "Unavailable", {"Retry-After": "120"}, None
        )
        self.assertEqual(WORKER.retry_after_seconds(seconds_error, 10), 120)
        date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
        date_error = urllib.error.HTTPError(
            "https://example.org/", 503, "Unavailable", {"Retry-After": date}, None
        )
        self.assertGreaterEqual(WORKER.retry_after_seconds(date_error, 10), 110)
        huge_error = urllib.error.HTTPError(
            "https://example.org/", 503, "Unavailable", {"Retry-After": "999999999"}, None
        )
        self.assertEqual(WORKER.retry_after_seconds(huge_error, 10), WORKER.MAX_RETRY_AFTER_SECONDS)

    def test_worker_page_and_depth_caps_are_bounded(self):
        with mock.patch.dict(os.environ, {"EH_CRAWL_MAX_PAGES": "9999"}):
            self.assertEqual(
                WORKER.bounded_int("EH_CRAWL_MAX_PAGES", 80, 1, WORKER.MAX_PAGES_HARD_CAP),
                WORKER.MAX_PAGES_HARD_CAP,
            )
        with mock.patch.dict(os.environ, {"EH_CRAWL_MAX_DEPTH": "999"}):
            self.assertEqual(
                WORKER.bounded_int("EH_CRAWL_MAX_DEPTH", 3, 0, WORKER.MAX_DEPTH_HARD_CAP),
                WORKER.MAX_DEPTH_HARD_CAP,
            )


if __name__ == "__main__":
    unittest.main()
