import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("connectors.py")
SPEC = importlib.util.spec_from_file_location("everythinghub_connectors", MODULE_PATH)
connectors = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connectors)


class ConnectorTests(unittest.TestCase):
    def test_normalize_url_removes_fragment_and_rejects_non_http(self):
        self.assertEqual(
            connectors.normalize_url("HTTPS://Example.com/page#part"),
            "https://example.com/page",
        )
        self.assertIsNone(connectors.normalize_url("javascript:alert(1)"))

    def test_sitemap_parser_accepts_same_host_urls_only(self):
        xml = b"""<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/a</loc></url>
          <url><loc>https://other.example/b</loc></url>
        </urlset>"""
        pages, children = connectors.parse_sitemap(xml, "https://example.com/")
        self.assertEqual(pages, ["https://example.com/a"])
        self.assertEqual(children, [])

    def test_sitemap_index_returns_same_host_children(self):
        xml = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.com/one.xml</loc></sitemap>
          <sitemap><loc>https://evil.example/two.xml</loc></sitemap>
        </sitemapindex>"""
        pages, children = connectors.parse_sitemap(xml, "https://example.com/")
        self.assertEqual(pages, [])
        self.assertEqual(children, ["https://example.com/one.xml"])


if __name__ == "__main__":
    unittest.main()
