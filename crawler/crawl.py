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

def classify_records(previous, records):
    """Merge fetched records and count only new or materially changed content."""
    merged = dict(previous)
    new_count = updated_count = unchanged_count = 0
    for url, record in records.items():
        prior = previous.get(url)
        if prior is None:
            new_count += 1
        elif any(prior.get(field) != record.get(field)
                 for field in ("title", "description", "text")):
            updated_count += 1
        else:
            unchanged_count += 1
        # Keep the latest fetch timestamp even when searchable content is unchanged.
        merged[url] = record
    return merged, new_count, updated_count, unchanged_count

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
        if isinstance(item, dict) and item.get("url"):
            queue.append((item["url"], int(item.get("depth", 0))))
    for seed in seeds:
        if seed not in seen and len(seen) < QUEUE_LIMIT:
            seen.add(seed)
            queue.append((seed, 0))

    # Do not flood the frontier with sitemap XML. The first crawl showed that
    # sitemap URLs consumed the 80-fetch budget before discovered HTML links
    # could be followed. Prioritize real same-host HTML links from fetched pages.
    records = {}
    fetched = 0
    depth_counts = {}
    results = []
    while queue and fetched < MAX_FETCH_PER_RUN:
        batch = []
        # Drain the frontier into depth buckets, then take one URL per depth
        # per pass (deepest first). This prevents a large shallow backlog from
        # blocking depth-2/depth-3 discovery for dozens of scheduled runs.
        pending = {}
        while queue:
            url, depth = queue.popleft()
            pending.setdefault(depth, deque()).append((url, depth))
        depth_order = sorted(pending, reverse=True)
        while depth_order and len(batch) < WORKERS and fetched + len(batch) < MAX_FETCH_PER_RUN:
            remaining_depths = []
            for depth in depth_order:
                if len(batch) >= WORKERS or fetched + len(batch) >= MAX_FETCH_PER_RUN:
                    break
                items = pending[depth]
                if items:
                    url, item_depth = items.popleft()
                    if item_depth <= MAX_DEPTH and allowed(url):
                        batch.append((url, item_depth))
                if items:
                    remaining_depths.append(depth)
            depth_order = remaining_depths
        # Keep unselected URLs persistent; preserve FIFO order within each depth.
        for depth in sorted(pending):
            queue.extend(pending[depth])
        if not batch:
            break

        def fetch(item):
            url, depth = item
            try:
                final, content_type, body = request(url)
                if content_type != "text/html":
                    return {"status": "non_html"}
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
                return {"status": "parsed", "record": {
                    "url": final, "title": title,
                    "description": parser.description[:500],
                    "text": text, "depth": depth, "fetched_at": int(time.time())
                }, "links": links}
            except Exception as error:
                print(f"skip {url}: {error}")
                return {"status": "error"}

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            batch_results = list(pool.map(fetch, batch))
        results.extend(batch_results)
        fetched += len(batch)
        for result in batch_results:
            if not result or result.get("status") != "parsed":
                continue
            record, links = result["record"], result["links"]
            records[record["url"]] = record
            depth_counts[str(record["depth"])] = depth_counts.get(str(record["depth"]), 0) + 1
            for candidate in links:
                if candidate not in seen and len(seen) < QUEUE_LIMIT:
                    seen.add(candidate)
                    queue.append((candidate, record["depth"] + 1))

    old = load_json(OUTPUT, {"version": 1, "pages": []})
    previous = {p["url"]: p for p in old.get("pages", []) if p.get("url")}
    merged = dict(previous)
    new_count = updated_count = unchanged_count = 0
    for url, record in records.items():
        prior = previous.get(url)
        if prior is None:
            new_count += 1
        elif any(prior.get(field) != record.get(field)
                 for field in ("title", "description", "text")):
            updated_count += 1
        else:
            unchanged_count += 1
        merged[url] = record
    parsed_count = len(records)
    error_count = sum(1 for result in results if result and result.get("status") == "error")
    non_html_count = sum(1 for result in results if result and result.get("status") == "non_html")
    indexed_success_count = new_count + updated_count
    run_metrics = {
        "fetch_attempts": fetched, "parsed_html_pages": parsed_count,
        "newly_indexed_records": new_count,
        "meaningfully_updated_records": updated_count,
        "unchanged_records": unchanged_count, "fetch_errors": error_count,
        "non_html_responses": non_html_count,
        "successful_indexed_records": indexed_success_count,
        "run_completed_at": int(time.time())
    }
    save_json(OUTPUT, {
        "version": 1, "updated_at": int(time.time()), "run_metrics": run_metrics,
        "pages_crawled_this_run": fetched,
        "pages_indexed_this_run": parsed_count,
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
    print(f"Attempts {fetched}; parsed {parsed_count}; new {new_count}; "
          f"updated {updated_count}; unchanged {unchanged_count}; errors {error_count}; "
          f"non-HTML {non_html_count}; successful indexed {indexed_success_count}; "
          f"frontier remaining {len(queue)}; total index {len(merged)}.")

if __name__ == "__main__":
    main()
