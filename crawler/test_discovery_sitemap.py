import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = "https://everythinghub.ink"
SITEMAP = ROOT / "vercel-homepage" / "sitemap.xml"
HOMEPAGE = ROOT / "vercel-homepage"


class PageMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.h1_parts = []
        self.canonical = ""
        self.robots = ""
        self._title_depth = 0
        self._h1_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._title_depth += 1
        elif tag == "h1":
            self._h1_depth += 1
        elif tag == "link":
            rel = {part.lower() for part in attrs.get("rel", "").split()}
            if "canonical" in rel:
                self.canonical = attrs.get("href", "").strip()
        elif tag == "meta" and attrs.get("name", "").strip().lower() == "robots":
            self.robots = attrs.get("content", "").strip().lower()

    def handle_endtag(self, tag):
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        elif tag == "h1" and self._h1_depth:
            self._h1_depth -= 1

    def handle_data(self, data):
        if self._title_depth:
            self.title_parts.append(data)
        if self._h1_depth:
            self.h1_parts.append(data)

    @property
    def title(self):
        return " ".join(" ".join(self.title_parts).split())

    @property
    def h1(self):
        return " ".join(" ".join(self.h1_parts).split())


def sitemap_urls():
    root = ET.parse(SITEMAP).getroot()
    return [
        (node.text or "").strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1].lower() == "loc"
    ]


def local_html_path(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "everythinghub.ink":
        raise ValueError(f"unexpected sitemap origin: {url}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"sitemap URL contains query or fragment: {url}")
    relative = unquote(parsed.path.lstrip("/"))
    parts = Path(relative).parts
    if any(part in (".", "..") for part in parts):
        raise ValueError(f"unsafe sitemap path: {url}")
    if not relative:
        return HOMEPAGE / "index.html"
    if parsed.path.endswith("/"):
        return HOMEPAGE.joinpath(*parts, "index.html")
    return HOMEPAGE.joinpath(*parts)


class DiscoverySitemapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.urls = sitemap_urls()

    def test_sitemap_urls_are_unique(self):
        duplicates = sorted({url for url in self.urls if self.urls.count(url) > 1})
        self.assertEqual(
            len(self.urls),
            len(set(self.urls)),
            f"sitemap contains duplicate URLs: {duplicates[:10]}",
        )

    def test_every_sitemap_url_maps_to_a_local_html_file(self):
        missing = []
        for url in self.urls:
            try:
                path = local_html_path(url)
            except ValueError as error:
                missing.append(str(error))
                continue
            if not path.is_file():
                missing.append(f"{url} -> {path.relative_to(ROOT)}")
        self.assertEqual(missing, [], f"unresolved sitemap entries: {missing[:20]}")

    def test_discovery_pages_have_matching_canonical_and_indexable_headings(self):
        failures = []
        checked = 0
        for url in self.urls:
            parsed_url = urlsplit(url)
            if not parsed_url.path.startswith("/discover/"):
                continue
            checked += 1
            path = local_html_path(url)
            if not path.is_file():
                continue
            parser = PageMetadataParser()
            parser.feed(path.read_text(encoding="utf-8"))
            if parser.canonical != url:
                failures.append(f"{url}: canonical={parser.canonical!r}")
            if not parser.title:
                failures.append(f"{url}: missing title")
            if not parser.h1:
                failures.append(f"{url}: missing h1")
            if "noindex" in parser.robots:
                failures.append(f"{url}: sitemap includes noindex page")
        self.assertGreater(checked, 0, "sitemap contains no discovery pages")
        self.assertEqual(failures, [], f"discovery metadata failures: {failures[:20]}")


if __name__ == "__main__":
    unittest.main()
