#!/usr/bin/env python3
"""Polite, resumable, depth-limited crawler for Everything Hub."""
import concurrent.futures
import json
import re
import time
import urllib.error
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
MAX_RUN_SECONDS = 360
HOST_MIN_INTERVAL = 1.0
ROBOTS_MAX_BYTES = 512_000
TIMEOUT = 15
UA = "EverythingHubCrawler/1.1 (+https://github.com/rshure001/EverythingHub-Crawler)"

class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent page fetches from silently crossing into an unchecked host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(target)
        if (old.scheme.lower(), old.netloc.lower()) != (new.scheme.lower(), new.netloc.lower()):
            raise urllib.error.HTTPError(
                req.full_url, code, "cross-origin redirect blocked", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, target)

HTTP_OPENER = urllib.request.build_opener(SameOriginRedirectHandler())

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
    with HTTP_OPENER.open(req, timeout=TIMEOUT) as response:
        return response.geturl(), response.headers.get_content_type(), response.read(2_000_000)

def origin(url):
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"

def effective_host_delay(parser):
    """Return the strictest robots.txt rate directive, with a safe minimum."""
    crawl_delay = parser.crawl_delay(UA) or 0
    request_rate = parser.request_rate(UA)
    rate_delay = (
        request_rate.seconds / request_rate.requests
        if request_rate and request_rate.requests
        else 0
    )
    return max(HOST_MIN_INTERVAL, float(crawl_delay), float(rate_delay))

def select_batch(queue, is_allowed, host_next_allowed, now, workers=WORKERS):
    """Select depth-prioritized work while limiting each batch to one URL per host."""
    pending = list(queue)
    queue.clear()
    pending.sort(key=lambda item: item[1], reverse=True)
    selected = []
    deferred = []
    selected_hosts = set()

    for index, (url, depth) in enumerate(pending):
        if depth > MAX_DEPTH:
            continue
        host = origin(url)
        next_allowed = float(host_next_allowed.get(host, 0) or 0)
        if host in selected_hosts or next_allowed > now:
            deferred.append((url, depth))
            continue
        if not is_allowed(url):
            continue
        selected.append((url, depth))
        selected_hosts.add(host)
        if len(selected) >= workers:
            deferred.extend(pending[index + 1:])
            break

    queue.extend(deferred)
    return selected

def next_host_wait(queue, host_next_allowed, now):
    """Seconds until the next queued host is eligible, or zero if immediately ready."""
    ready_times = []
    for url, _depth in queue:
        ready_at = float(host_next_allowed.get(origin(url), 0) or 0)
        if ready_at <= now:
            return 0.0
        ready_times.append(ready_at)
    return max(0.0, min(ready_times) - now) if ready_times else 0.0

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
    robots_blocked_count = [0]

    def policy(site):
        if site not in robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(site + "/robots.txt")
            try:
                req = urllib.request.Request(site + "/robots.txt", headers={
                    "User-Agent": UA, "Accept": "text/plain,*/*"
                })
                with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"robots.txt returned HTTP {response.status}")
                    body = response.read(ROBOTS_MAX_BYTES).decode("utf-8", "replace")
                parser.parse(body.splitlines())
            except Exception:
                # If robots.txt cannot be read, fail closed for this host.
                parser.disallow_all = True
            robots[site] = parser
        return robots[site]

    def allowed(url):
        permitted = policy(origin(url)).can_fetch(UA, url)
        if not permitted:
            robots_blocked_count[0] += 1
        return permitted

    def host_delay(site):
        return effective_host_delay(policy(site))

    # Resume durable frontier. Seed pages are only inserted once, not on every run.
    frontier = load_json(FRONTIER, {"queue": [], "seen": []})
    seen = set(frontier.get("seen", []))
    saved_host_schedule = frontier.get("host_next_allowed", {})
    if not isinstance(saved_host_schedule, dict):
        saved_host_schedule = {}
    host_next_allowed = {
        str(host): float(timestamp)
        for host, timestamp in saved_host_schedule.items()
        if isinstance(timestamp, (int, float))
    }
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
    requests_by_host = {}
    run_started = time.monotonic()

    while queue and fetched < MAX_FETCH_PER_RUN:
        elapsed = time.monotonic() - run_started
        if elapsed >= MAX_RUN_SECONDS:
            break

        batch = select_batch(queue, allowed, host_next_allowed, time.time())
        if not batch:
            wait = next_host_wait(queue, host_next_allowed, time.time())
            remaining = MAX_RUN_SECONDS - (time.monotonic() - run_started)
            if wait > 0 and wait <= remaining:
                time.sleep(wait)
                continue
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

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, len(batch))) as pool:
            results = list(pool.map(fetch, batch))

        fetched += len(batch)
        completed_at = time.time()
        for url, _depth in batch:
            host = origin(url)
            host_next_allowed[host] = completed_at + host_delay(host)
            requests_by_host[host] = requests_by_host.get(host, 0) + 1

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

    run_duration = round(time.monotonic() - run_started, 2)
    old = load_json(OUTPUT, {"version": 1, "pages": []})
    merged = {p["url"]: p for p in old.get("pages", []) if p.get("url")}
    merged.update(records)
    save_json(OUTPUT, {
        "version": 1, "updated_at": int(time.time()),
        "pages_crawled_this_run": fetched,
        "pages_indexed_this_run": len(records),
        "pages_not_indexed_this_run": fetched - len(records),
        "robots_blocked_this_run": robots_blocked_count[0],
        "run_duration_seconds": run_duration,
        "host_requests_this_run": requests_by_host,
        "depth_counts_this_run": depth_counts,
        "queue_remaining": len(queue),
        "urls_discovered_total": len(seen),
        "queue_limit": QUEUE_LIMIT, "max_depth": MAX_DEPTH,
        "pages": list(merged.values())
    })
    save_json(FRONTIER, {
        "updated_at": int(time.time()),
        "queue": [{"url": url, "depth": depth} for url, depth in queue],
        "seen": sorted(seen),
        "host_next_allowed": host_next_allowed
    })
    print(f"Fetched {fetched}; indexed {len(records)}; not indexed {fetched - len(records)}; "
          f"robots blocked {robots_blocked_count[0]}; duration {run_duration:.2f}s; "
          f"depth counts {depth_counts}; frontier remaining {len(queue)}; "
          f"discovered {len(seen)} URLs; total index {len(merged)}.")

if __name__ == "__main__":
    main()
