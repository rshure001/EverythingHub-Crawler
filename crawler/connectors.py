#!/usr/bin/env python3
"""Discover public URLs through publisher-declared robots.txt sitemaps.

This is URL discovery only. The normal crawler still checks robots.txt before
fetching each page, applies its per-run fetch cap, and stores page records.
"""
import json
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "crawler" / "connectors.json"
FRONTIER = ROOT / "data" / "frontier.json"
USER_AGENT = "EverythingHubCrawler/1.1 (+https://github.com/rshure001/EverythingHub-Crawler)"
TIMEOUT = 12
MAX_RESPONSE_BYTES = 2_000_000
MAX_SITEMAPS_PER_CONNECTOR = 4
MAX_URLS_PER_CONNECTOR = 250
MAX_NEW_URLS_PER_RUN = 2_500
MAX_FRONTIER_URLS = 50_000


def normalize_url(value):
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
        parsed.query, ""
    ))


def same_host(url, base_url):
    return urllib.parse.urlsplit(url).netloc.lower() == urllib.parse.urlsplit(base_url).netloc.lower()


def read_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/plain,*/*"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        content_type = response.headers.get_content_type()
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response exceeds size limit")
    return content_type, body


def parse_sitemap(body, base_url):
    """Return (page URLs, child sitemap URLs), constrained to the connector host."""
    root = ET.fromstring(body)
    pages, children = [], []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag != "loc" or not element.text:
            continue
        url = normalize_url(element.text)
        if not url or not same_host(url, base_url):
            continue
        parent_tag = element.getparent().tag if hasattr(element, "getparent") else ""
        # ElementTree has no parent pointer; classify from the root document type.
        if root.tag.rsplit("}", 1)[-1].lower() == "sitemapindex":
            children.append(url)
        else:
            pages.append(url)
    return pages, children


def robots_sitemaps(base_url):
    origin = urllib.parse.urlsplit(base_url)
    robots_url = urllib.parse.urlunsplit((origin.scheme, origin.netloc, "/robots.txt", "", ""))
    content_type, body = read_url(robots_url)
    text = body.decode("utf-8", "replace")
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(text.splitlines())
    declared = []
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "sitemap":
            candidate = normalize_url(value.strip())
            if candidate and same_host(candidate, base_url) and parser.can_fetch(USER_AGENT, candidate):
                declared.append(candidate)
    if not declared:
        fallback = normalize_url(urllib.parse.urljoin(base_url, "/sitemap.xml"))
        if fallback and parser.can_fetch(USER_AGENT, fallback):
            declared.append(fallback)
    return parser, list(dict.fromkeys(declared))


def load_frontier():
    try:
        payload = json.loads(FRONTIER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"queue": [], "seen": []}
    payload.setdefault("queue", [])
    payload.setdefault("seen", [])
    return payload


def main():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    limits = registry.get("limits", {})
    max_sitemaps = min(int(limits.get("max_sitemaps_per_connector", MAX_SITEMAPS_PER_CONNECTOR)), MAX_SITEMAPS_PER_CONNECTOR)
    max_per_connector = min(int(limits.get("max_urls_per_connector", MAX_URLS_PER_CONNECTOR)), MAX_URLS_PER_CONNECTOR)
    max_new = min(int(limits.get("max_new_urls_per_run", MAX_NEW_URLS_PER_RUN)), MAX_NEW_URLS_PER_RUN)
    max_frontier = min(int(limits.get("max_frontier_urls", MAX_FRONTIER_URLS)), MAX_FRONTIER_URLS)

    frontier = load_frontier()
    seen = set(frontier["seen"])
    queued = {item.get("url") for item in frontier["queue"] if isinstance(item, dict)}
    added_total = 0
    counts = Counter()

    for connector in registry.get("connectors", []):
        if not connector.get("enabled"):
            continue
        base_url = normalize_url(connector.get("base_url", ""))
        if not base_url:
            continue
        connector_id = connector.get("id", "unknown")
        try:
            robots, sitemap_queue = robots_sitemaps(base_url)
            sitemap_count = 0
            discovered = []
            while sitemap_queue and sitemap_count < max_sitemaps and len(discovered) < max_per_connector:
                sitemap_url = sitemap_queue.pop(0)
                if not same_host(sitemap_url, base_url) or not robots.can_fetch(USER_AGENT, sitemap_url):
                    continue
                try:
                    _, body = read_url(sitemap_url)
                    pages, children = parse_sitemap(body, base_url)
                    discovered.extend(pages)
                    sitemap_queue.extend(children[:max_sitemaps])
                    sitemap_count += 1
                except Exception as error:
                    print(f"connector {connector_id}: skipped sitemap {sitemap_url}: {error}")
            added = 0
            for url in discovered:
                if added_total >= max_new or len(seen) >= max_frontier:
                    break
                if url not in seen and url not in queued:
                    seen.add(url)
                    queued.add(url)
                    frontier["queue"].append({"url": url, "depth": 0, "source": f"connector:{connector_id}"})
                    added += 1
                    added_total += 1
                if added >= max_per_connector:
                    break
            counts[connector_id] = added
            print(f"connector {connector_id}: discovered {len(discovered)}, queued {added}")
        except Exception as error:
            print(f"connector {connector_id}: unavailable this run: {error}")
            counts[connector_id] = 0

    frontier["seen"] = sorted(seen)
    frontier["updated_at"] = int(time.time())
    FRONTIER.parent.mkdir(parents=True, exist_ok=True)
    temp = FRONTIER.with_suffix(".json.tmp")
    temp.write_text(json.dumps(frontier, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(FRONTIER)
    print(f"Connector discovery complete: {added_total:,} new URLs queued across {len(counts)} connectors.")


if __name__ == "__main__":
    main()
