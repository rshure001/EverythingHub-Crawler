#!/usr/bin/env python3
"""Bounded sitemap crawler for Everything Hub. Standard library only."""
import concurrent.futures
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "crawler" / "seeds.txt"
OUTPUT = ROOT / "data" / "index.json"
MAX_WORKERS = 8
PAGES_PER_WORKER = 10
MAX_PAGES = MAX_WORKERS * PAGES_PER_WORKER
TIMEOUT = 15
UA = "EverythingHubCrawler/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"

class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = False
        self.title_text = []
        self.description = ""
        self.text = []
        self.in_body = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "title": self.title = True
        if tag.lower() == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content", "")
        if tag.lower() == "body": self.in_body = True
    def handle_endtag(self, tag):
        if tag.lower() == "title": self.title = False
        if tag.lower() == "body": self.in_body = False
    def handle_data(self, data):
        if self.title: self.title_text.append(data)
        if self.in_body and data.strip(): self.text.append(data.strip())

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        data = response.read(2_000_000)
        if response.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return response.geturl(), response.headers.get_content_type(), data

def sitemap_urls(url, seen=None):
    seen = seen or set()
    if url in seen: return []
    seen.add(url)
    try:
        final_url, kind, raw = get(url)
        root = ET.fromstring(raw)
    except Exception as exc:
        print(f"sitemap skipped: {url}: {exc}")
        return []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    found = []
    for loc in root.findall(".//sm:loc", ns):
        if not loc.text: continue
        item = loc.text.strip()
        if root.tag.endswith("sitemapindex"):
            found.extend(sitemap_urls(item, seen))
        else:
            found.append(item)
        if len(found) >= MAX_PAGES: break
    return found

def allowed(url, cache):
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try: rp.read()
        except Exception: pass
        cache[origin] = rp
    return cache[origin].can_fetch(UA, url)

def fetch_page(url):
    try:
        final_url, kind, raw = get(url)
        if kind != "text/html": return None
        parser = MetaParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        title = re.sub(r"\s+", " ", " ".join(parser.title_text)).strip()
        text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()[:1200]
        return {"url": final_url, "title": title, "description": parser.description[:500], "text": text, "fetched_at": int(time.time())}
    except Exception as exc:
        print(f"page skipped: {url}: {exc}")
        return None

def main():
    seeds = [x.strip() for x in SEEDS.read_text().splitlines() if x.strip() and not x.startswith("#")]
    urls = []
    for seed in seeds:
        urls.extend(sitemap_urls(seed))
        if len(urls) >= MAX_PAGES: break
    urls = list(dict.fromkeys(urls))[:MAX_PAGES]
    robots = {}
    urls = [u for u in urls if allowed(u, robots)]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try: existing = json.loads(OUTPUT.read_text())
    except Exception: existing = {"version": 1, "pages": []}
    records = {p.get("url"): p for p in existing.get("pages", []) if p.get("url")}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for item in pool.map(fetch_page, urls):
            if item: records[item["url"]] = item
    result = {"version": 1, "updated_at": int(time.time()), "pages_crawled_this_run": len(urls), "pages": list(records.values())}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Fetched {len(urls)} candidate pages; {len(records)} indexed records total.")

if __name__ == "__main__":
    main()
