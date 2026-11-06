#!/usr/bin/env python3
"""Polite, resumable, depth-limited crawler for Everything Hub."""
import concurrent.futures
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "crawler" / "seeds.txt"
OUTPUT = ROOT / "data" / "index.json"
FRONTIER = ROOT / "data" / "frontier.json"
WORKERS = 8
QUEUE_LIMIT = 50000
MAX_DEPTH = 3
MAX_FETCH_PER_RUN = 80
TIMEOUT = 15
UA = "EverythingHubCrawler/1.1 (+https://github.com/rshure001/EverythingHub-Crawler)"

class PageParser(HTMLParser):
    """Extract readable text and links while excluding repeated page chrome."""
    def __init__(self):
        super().__init__()
        self.title_on = False
        self.title = []
        self.description = ""
        self.text = []
        self.links = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = dict(attrs)
        if tag == "title":
            self.title_on = True
        if tag == "meta" and a.get("name", "").lower() == "description":
            self.description = a.get("content", "")
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self.skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.title_on = False
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header") and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.title_on:
            self.title.append(data)
        if not self.skip_depth and data.strip():
            self.text.append(data.strip())

def clean(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    # Remove fragments and common tracking parameters; retain meaningful query parameters.
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")
             and k.lower() not in ("fbclid", "gclid", "mc_cid", "mc_eid")]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/",
                                    urllib.parse.urlencode(query), ""))

def request(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xml,*/*"
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.geturl(), response.headers.get_content_type(), response.read(2_000_000)

def origin(url):
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"

def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback

def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)

def main():
    seeds = [clean(x.strip()) for x in SEEDS.read_text(encoding="utf-8").splitlines()
             if x.strip() and not x.startswith("#")]
    seeds = list(dict.fromkeys(x for x in seeds if x))
    robots = {}

    def allowed(url):
        site = origin(url)
        if site not in robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(site + "/robots.txt")
            try:
                parser.read()
            except Exception:
                # If robots.txt cannot be read, fail closed for this host.
                parser.disallow_all = True
            robots[site] = parser
        return robots[site].can_fetch(UA, url)

    # Resume durable frontier. Seed pages are only inserted once, not on every run.
    frontier = load_json(FRONTIER, {"queue": [], "seen": []})
    seen = set(frontier.get("seen", []))
    queue = deque()
    for item in frontier.get("queue", []):
        if isinstance(item, dict) and item.get("url") and item["url"] not in set():
            queue.append((item["url"], int(item.get("depth", 0))))
    for seed in seeds:
        if seed not in seen and len(seen) < QUEUE_LIMIT:
            seen.add(seed)
            queue.append((seed, 0))

    # Discover sitemap URLs for newly seeded hosts. Keep sitemap work bounded.
    seeded_hosts = {origin(s) for s in seeds}
    for seed in seeds:
        site = origin(seed)
        if not allowed(site + "/robots.txt"):
            continue
        sitemap_urls = [site + "/sitemap.xml"]
        try:
            _, _, body = request(site + "/robots.txt")
            for line in body.decode("utf-8", "ignore").splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_urls.insert(0, line.split(":", 1)[1].strip())
        except Exception:
            pass
        for sitemap in dict.fromkeys(sitemap_urls):
            if not allowed(sitemap):
                continue
            try:
                _, content_type, body = request(sitemap)
                if "xml" not in content_type and not body.lstrip().startswith(b"<?xml"):
                    continue
                root = ET.fromstring(body)
                ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for loc in root.findall(".//s:loc", ns):
                    candidate = clean(loc.text.strip()) if loc.text else None
                    if candidate and origin(candidate) == site and candidate not in seen and len(seen) < QUEUE_LIMIT:
                        seen.add(candidate)
                        queue.append((candidate, 1))
            except Exception:
                continue

    records = {}
    fetched = 0
    depth_counts = {}
    while queue and fetched < MAX_FETCH_PER_RUN:
        batch = []
        while queue and len(batch) < WORKERS and fetched + len(batch) < MAX_FETCH_PER_RUN:
            url, depth = queue.popleft()
            if depth <= MAX_DEPTH and allowed(url):
                batch.append((url, depth))
        if not batch:
            break

        def fetch(item):
            url, depth = item
            try:
                final, content_type, body = request(url)
                if content_type != "text/html":
                    return None
                parser = PageParser()
                parser.feed(body.decode("utf-8", "replace"))
                title = re.sub(r"\s+", " ", " ".join(parser.title)).strip()
                text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()[:4000]
                links = []
                if depth < MAX_DEPTH:
                    host = urllib.parse.urlsplit(final).netloc
                    for href in parser.links:
                        candidate = clean(urllib.parse.urljoin(final, href))
                        if candidate and urllib.parse.urlsplit(candidate).netloc == host:
                            links.append(candidate)
                return ({
                    "url": final, "title": title,
                    "description": parser.description[:500],
                    "text": text, "depth": depth, "fetched_at": int(time.time())
                }, links)
            except Exception as error:
                print(f"skip {url}: {error}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(fetch, batch))
        fetched += len(batch)
        for result in results:
            if not result:
                continue
            record, links = result
            records[record["url"]] = record
            depth_counts[str(record["depth"])] = depth_counts.get(str(record["depth"]), 0) + 1
            for candidate in links:
                if candidate not in seen and len(seen) < QUEUE_LIMIT:
                    seen.add(candidate)
                    queue.append((candidate, record["depth"] + 1))

    old = load_json(OUTPUT, {"version": 1, "pages": []})
    merged = {p["url"]: p for p in old.get("pages", []) if p.get("url")}
    merged.update(records)
    save_json(OUTPUT, {
        "version": 1, "updated_at": int(time.time()),
        "pages_crawled_this_run": fetched,
        "pages_indexed_this_run": len(records),
        "depth_counts_this_run": depth_counts,
        "queue_remaining": len(queue),
        "urls_discovered_total": len(seen),
        "queue_limit": QUEUE_LIMIT, "max_depth": MAX_DEPTH,
        "pages": list(merged.values())
    })
    save_json(FRONTIER, {
        "updated_at": int(time.time()),
        "queue": [{"url": url, "depth": depth} for url, depth in queue],
        "seen": sorted(seen)
    })
    print(f"Fetched {fetched}; indexed {len(records)}; depth counts {depth_counts}; "
          f"frontier remaining {len(queue)}; discovered {len(seen)} URLs; "
          f"total index {len(merged)}.")

if __name__ == "__main__":
    main()
