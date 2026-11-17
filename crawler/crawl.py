#!/usr/bin/env python3
"""Polite, resumable, depth-limited crawler for Everything Hub."""
import concurrent.futures
import email.utils
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from lane_classifier import VALID_LANES, classify_lane

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "crawler" / "seeds.txt"
OUTPUT = ROOT / "data" / "index.json"
FRONTIER = ROOT / "data" / "frontier.json"
WORKERS = 8
QUEUE_LIMIT = 50000
MAX_DEPTH = 3
MAX_FETCH_PER_RUN = 80
MAX_SITEMAP_HOSTS_PER_RUN = 3
MAX_SITEMAP_CHILD_SITEMAPS = 2
MAX_SITEMAP_URLS_PER_HOST = 100
MAX_SITEMAP_BYTES = 1_000_000
SITEMAP_RECHECK_SECONDS = 7 * 24 * 60 * 60
MAX_RETRY_AFTER_SECONDS = 7 * 24 * 60 * 60
DEFAULT_429_BACKOFF_SECONDS = 3600
DEFAULT_5XX_BACKOFF_SECONDS = 600
DEFAULT_NETWORK_BACKOFF_SECONDS = 60
MAX_RUN_SECONDS = 360
HOST_MIN_INTERVAL = 1.0
ROBOTS_MAX_BYTES = 512_000
TIMEOUT = 15
UA = "EverythingHubCrawler/1.1 (+https://github.com/rshure001/EverythingHub-Crawler)"

class RobotsAwareRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow same-host redirects only after checking robots.txt for the target URL."""
    def __init__(self, is_allowed):
        super().__init__()
        self.is_allowed = is_allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        old_host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
        new_host = (urllib.parse.urlsplit(target).hostname or "").lower()
        if old_host != new_host:
            raise urllib.error.HTTPError(
                req.full_url, code, "cross-host redirect blocked", headers, fp
            )
        if not self.is_allowed(target):
            raise urllib.error.HTTPError(
                req.full_url, code, "redirect target blocked by robots.txt", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, target)

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

def request(url, is_allowed, max_bytes=2_000_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xml,*/*"
    })
    opener = urllib.request.build_opener(RobotsAwareRedirectHandler(is_allowed))
    with opener.open(req, timeout=TIMEOUT) as response:
        return response.geturl(), response.headers.get_content_type(), response.read(max_bytes)

def origin(url):
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def load_robots_policy(site):
    """Fetch robots.txt with a bounded read; deny the host if policy cannot be read."""
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(site.rstrip("/") + "/robots.txt")
    try:
        req = urllib.request.Request(site.rstrip("/") + "/robots.txt", headers={
            "User-Agent": UA, "Accept": "text/plain,*/*"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            if response.status >= 400:
                raise RuntimeError(f"robots.txt returned HTTP {response.status}")
            raw = response.read(ROBOTS_MAX_BYTES + 1)
            if len(raw) > ROBOTS_MAX_BYTES:
                raise RuntimeError("robots.txt exceeds the safe size limit")
        body = raw.decode("utf-8", "replace")
        parser.parse(body.splitlines())
        parser.everythinghub_groups = parse_robots_groups(body)
    except urllib.error.HTTPError as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = f"http_{error.code}"
    except Exception as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = (
            "too_large" if "safe size limit" in str(error) else "unavailable"
        )
    return parser


def parse_robots_groups(body):
    """Parse robots groups while preserving Allow/Disallow rules for RFC-style matching."""
    groups = []
    agents = []
    rules = []

    def flush():
        if agents:
            groups.append({"agents": list(agents), "rules": list(rules)})

    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            if agents:
                flush()
                agents = []
                rules = []
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
                agents = []
                rules = []
            if value:
                agents.append(value.lower())
        elif key in ("allow", "disallow") and agents:
            if key == "disallow" and not value:
                continue
            if value:
                rules.append({"pattern": value, "allow": key == "allow"})
    flush()
    return groups


def _robots_normalize_component(value, safe):
    """Normalize unreserved percent escapes and encode non-ASCII path octets."""
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"

    def normalize_escape(match):
        octet = chr(int(match.group(1), 16))
        return octet if octet in unreserved else "%" + match.group(1).upper()

    value = re.sub(r"%([0-9a-fA-F]{2})", normalize_escape, value)
    return urllib.parse.quote(value, safe=safe)


def robots_can_fetch(parser, useragent, url):
    """Apply the most-specific matching robots rule; Allow wins equal-specificity ties."""
    if parser.disallow_all:
        return False
    if parser.allow_all:
        return True

    groups = getattr(parser, "everythinghub_groups", None)
    if groups is None:
        return parser.can_fetch(useragent, url)

    product_parts = useragent.split("/", 1)[0].split()
    product = product_parts[0].lower() if product_parts else ""
    matching_groups = []
    for group in groups:
        scores = [
            len(agent)
            for agent in group["agents"]
            if agent == "*" or (agent and agent in product)
        ]
        if scores:
            matching_groups.append((max(scores), group))

    specific = [item for item in matching_groups if item[0] > 0]
    if specific:
        best_agent_score = max(score for score, _group in specific)
        selected = [group for score, group in specific if score == best_agent_score]
    else:
        selected = [group for score, group in matching_groups if score == 0]
    if not selected:
        return True

    parsed = urllib.parse.urlsplit(url)
    target = _robots_normalize_component(
        parsed.path or "/", safe="/%:@!def load_robots_policy(site):
    """Fetch robots.txt with a bounded read; deny the host if policy cannot be read."""
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(site.rstrip("/") + "/robots.txt")
    try:
        req = urllib.request.Request(site.rstrip("/") + "/robots.txt", headers={
            "User-Agent": UA, "Accept": "text/plain,*/*"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            if response.status >= 400:
                raise RuntimeError(f"robots.txt returned HTTP {response.status}")
            raw = response.read(ROBOTS_MAX_BYTES + 1)
            if len(raw) > ROBOTS_MAX_BYTES:
                raise RuntimeError("robots.txt exceeds the safe size limit")
        parser.parse(raw.decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = f"http_{error.code}"
    except Exception as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = (
            "too_large" if "safe size limit" in str(error) else "unavailable"
        )
    return parser
'()*+,;=-._~"
    )
    if parsed.query:
        target += "?" + _robots_normalize_component(
            parsed.query, safe="/?%:@!def load_robots_policy(site):
    """Fetch robots.txt with a bounded read; deny the host if policy cannot be read."""
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(site.rstrip("/") + "/robots.txt")
    try:
        req = urllib.request.Request(site.rstrip("/") + "/robots.txt", headers={
            "User-Agent": UA, "Accept": "text/plain,*/*"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            if response.status >= 400:
                raise RuntimeError(f"robots.txt returned HTTP {response.status}")
            raw = response.read(ROBOTS_MAX_BYTES + 1)
            if len(raw) > ROBOTS_MAX_BYTES:
                raise RuntimeError("robots.txt exceeds the safe size limit")
        parser.parse(raw.decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = f"http_{error.code}"
    except Exception as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = (
            "too_large" if "safe size limit" in str(error) else "unavailable"
        )
    return parser
'()*+,;=-._~"
        )

    matches = []
    for group in selected:
        for rule in group["rules"]:
            pattern = rule["pattern"]
            anchored = pattern.endswith("$")
            raw_pattern = pattern[:-1] if anchored else pattern
            normalized = _robots_normalize_component(
                raw_pattern, safe="/%:@!def load_robots_policy(site):
    """Fetch robots.txt with a bounded read; deny the host if policy cannot be read."""
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(site.rstrip("/") + "/robots.txt")
    try:
        req = urllib.request.Request(site.rstrip("/") + "/robots.txt", headers={
            "User-Agent": UA, "Accept": "text/plain,*/*"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            if response.status >= 400:
                raise RuntimeError(f"robots.txt returned HTTP {response.status}")
            raw = response.read(ROBOTS_MAX_BYTES + 1)
            if len(raw) > ROBOTS_MAX_BYTES:
                raise RuntimeError("robots.txt exceeds the safe size limit")
        parser.parse(raw.decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = f"http_{error.code}"
    except Exception as error:
        parser.disallow_all = True
        parser.everythinghub_policy_error = (
            "too_large" if "safe size limit" in str(error) else "unavailable"
        )
    return parser
'()*+,;=-._~"
            )
            regex = "^" + re.escape(normalized).replace(r"\*", ".*")
            if anchored:
                regex += "$"
            if re.match(regex, target):
                specificity = len(urllib.parse.unquote_to_bytes(normalized.replace("*", "")))
                matches.append((specificity, rule["allow"]))

    if not matches:
        return True
    longest = max(specificity for specificity, _allow in matches)
    return any(allow for specificity, allow in matches if specificity == longest)

def host_key(url):
    p = urllib.parse.urlsplit(url)
    return (p.hostname or p.netloc).lower()

def retry_after_seconds(error, default_seconds=0):
    """Parse Retry-After seconds or HTTP-date, with a bounded safe fallback."""
    headers = getattr(error, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    seconds = None
    if raw:
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                retry_at = email.utils.parsedate_to_datetime(str(raw))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = None
    if seconds is None:
        seconds = default_seconds
    return min(max(0.0, float(seconds)), float(MAX_RETRY_AFTER_SECONDS))


def parse_sitemap_xml(body, sitemap_url, max_urls=MAX_SITEMAP_URLS_PER_HOST):
    """Extract bounded same-host page URLs or child sitemaps from XML."""
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError, TypeError):
        return [], []

    def local_name(tag):
        return tag.rsplit("}", 1)[-1].lower()

    def location(node):
        for child in node:
            if local_name(child.tag) == "loc" and child.text:
                return child.text.strip()
        return ""

    reference_netloc = urllib.parse.urlsplit(sitemap_url).netloc.lower()
    pages = []
    child_sitemaps = []
    root_name = local_name(root.tag)

    if root_name == "urlset":
        for node in root:
            if local_name(node.tag) != "url":
                continue
            candidate = clean(location(node))
            if candidate and urllib.parse.urlsplit(candidate).netloc.lower() == reference_netloc:
                pages.append(candidate)
                if len(pages) >= max_urls:
                    break
    elif root_name == "sitemapindex":
        for node in root:
            if local_name(node.tag) != "sitemap":
                continue
            candidate = clean(location(node))
            if candidate and urllib.parse.urlsplit(candidate).netloc.lower() == reference_netloc:
                child_sitemaps.append(candidate)
                if len(child_sitemaps) >= MAX_SITEMAP_CHILD_SITEMAPS:
                    break

    return list(dict.fromkeys(pages)), list(dict.fromkeys(child_sitemaps))


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
        host = host_key(url)
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
        ready_at = float(host_next_allowed.get(host_key(url), 0) or 0)
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
    robots_policy_failures = {}
    robots_blocked_count = [0]
    rate_limited_count = [0]

    def policy(site):
        if site not in robots:
            parser = load_robots_policy(site)
            robots[site] = parser
            failure = getattr(parser, "everythinghub_policy_error", None)
            if failure:
                robots_policy_failures[host_key(site)] = failure
        return robots[site]

    def allowed(url):
        permitted = robots_can_fetch(policy(origin(url)), UA, url)
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
    sitemap_checked = frontier.get("sitemap_checked", {})
    if not isinstance(sitemap_checked, dict):
        sitemap_checked = {}
    host_next_allowed = {}
    for saved_host, timestamp in saved_host_schedule.items():
        if not isinstance(timestamp, (int, float)):
            continue
        key = str(saved_host).lower()
        if "://" in key:
            parsed = urllib.parse.urlsplit(key)
            key = (parsed.hostname or parsed.netloc).lower()
        host_next_allowed[key] = max(host_next_allowed.get(key, 0), float(timestamp))
    queue = deque()
    for item in frontier.get("queue", []):
        if isinstance(item, dict) and item.get("url"):
            queue.append((item["url"], int(item.get("depth", 0))))
    for seed in seeds:
        if seed not in seen and len(seen) < QUEUE_LIMIT:
            seen.add(seed)
            queue.append((seed, 0))

    # Sitemap discovery is deliberately separate from the page-fetch budget:
    # use only robots.txt declarations, stay on the same host, and cap every run.
    run_started = time.monotonic()
    sitemap_hosts_checked = 0
    sitemap_fetches = 0
    sitemap_urls_added = 0
    transient_retries_queued = 0
    candidate_hosts = []
    now_epoch = time.time()
    for seed in seeds:
        host = host_key(seed)
        try:
            checked_at = float(sitemap_checked.get(host, 0) or 0)
        except (TypeError, ValueError):
            checked_at = 0
        if host not in {item[0] for item in candidate_hosts} and now_epoch - checked_at >= SITEMAP_RECHECK_SECONDS:
            candidate_hosts.append((host, origin(seed)))
            if len(candidate_hosts) >= MAX_SITEMAP_HOSTS_PER_RUN:
                break

    for host, site in candidate_hosts:
        parser = policy(site)
        checked_now = int(time.time())
        sitemap_hosts_checked += 1
        if getattr(parser, "disallow_all", False):
            # Retry a robots.txt read failure after one hour, not every run.
            sitemap_checked[host] = checked_now - SITEMAP_RECHECK_SECONDS + 3600
            continue
        sitemap_checked[host] = checked_now

        declared = parser.site_maps() or []
        sitemap_url = None
        for raw_url in declared:
            candidate = clean(raw_url)
            if candidate and urllib.parse.urlsplit(candidate).netloc.lower() == urllib.parse.urlsplit(site).netloc.lower():
                sitemap_url = candidate
                break
        if not sitemap_url:
            continue

        pending_sitemaps = [sitemap_url]
        discovered_pages = []
        sitemap_index = 0

        def wait_for_sitemap_host():
            wait = max(0.0, float(host_next_allowed.get(host, 0) or 0) - time.time())
            remaining = MAX_RUN_SECONDS - (time.monotonic() - run_started)
            if wait > remaining:
                return False
            if wait > 0:
                time.sleep(wait)
            return True

        while sitemap_index < len(pending_sitemaps) and sitemap_index <= MAX_SITEMAP_CHILD_SITEMAPS:
            current_sitemap = pending_sitemaps[sitemap_index]
            sitemap_index += 1
            if not wait_for_sitemap_host():
                break
            if not allowed(current_sitemap):
                continue
            sitemap_fetches += 1
            try:
                final_sitemap, _content_type, body = request(
                    current_sitemap, allowed, max_bytes=MAX_SITEMAP_BYTES + 1
                )
            except Exception as error:
                status = getattr(error, "code", None)
                if status in (429, 503):
                    rate_limited_count[0] += 1
                if status == 429:
                    backoff = max(DEFAULT_429_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_429_BACKOFF_SECONDS))
                elif status in (500, 502, 503, 504):
                    backoff = max(DEFAULT_5XX_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_5XX_BACKOFF_SECONDS))
                elif not isinstance(error, urllib.error.HTTPError) and isinstance(error, urllib.error.URLError):
                    backoff = max(DEFAULT_NETWORK_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_NETWORK_BACKOFF_SECONDS))
                else:
                    backoff = host_delay(origin(current_sitemap))
                host_next_allowed[host] = time.time() + max(host_delay(origin(current_sitemap)), backoff)
                if status == 403:
                    cooldown = SITEMAP_RECHECK_SECONDS
                else:
                    cooldown = max(DEFAULT_NETWORK_BACKOFF_SECONDS, backoff)
                sitemap_checked[host] = int(
                    time.time() - SITEMAP_RECHECK_SECONDS + min(cooldown, MAX_RETRY_AFTER_SECONDS)
                )
                print(f"skip sitemap {current_sitemap}: {error}; host cooldown {backoff:.0f}s")
                if status in (403, 429, 500, 502, 503, 504) or (
                    not isinstance(error, urllib.error.HTTPError) and isinstance(error, urllib.error.URLError)
                ):
                    break
                continue
            host_next_allowed[host] = time.time() + max(host_delay(origin(current_sitemap)), host_delay(origin(final_sitemap)))
            if len(body) > MAX_SITEMAP_BYTES:
                print(f"skip sitemap {current_sitemap}: exceeds {MAX_SITEMAP_BYTES} bytes")
                continue

            page_urls, child_sitemaps = parse_sitemap_xml(
                body, final_sitemap, max_urls=MAX_SITEMAP_URLS_PER_HOST
            )
            discovered_pages.extend(page_urls)
            if sitemap_index == 1:
                pending_sitemaps.extend(child_sitemaps[:MAX_SITEMAP_CHILD_SITEMAPS])
            if len(discovered_pages) >= MAX_SITEMAP_URLS_PER_HOST:
                break

        for candidate in list(dict.fromkeys(discovered_pages))[:MAX_SITEMAP_URLS_PER_HOST]:
            if candidate not in seen and len(seen) < QUEUE_LIMIT:
                seen.add(candidate)
                queue.append((candidate, 1))
                sitemap_urls_added += 1

    records = {}
    fetched = 0
    depth_counts = {}
    requests_by_host = {}

    while queue and fetched < MAX_FETCH_PER_RUN:
        elapsed = time.monotonic() - run_started
        if elapsed >= MAX_RUN_SECONDS:
            break

        remaining_fetches = MAX_FETCH_PER_RUN - fetched
        batch = select_batch(
            queue, allowed, host_next_allowed, time.time(),
            workers=min(WORKERS, remaining_fetches)
        )
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
                final, content_type, body = request(url, allowed)
                delay = max(host_delay(origin(url)), host_delay(origin(final)))
                if content_type != "text/html":
                    return None, None, delay, False
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
                    "url": final, "title": title, "lane": classify_lane(final),
                    "description": parser.description[:500],
                    "text": text, "depth": depth, "fetched_at": int(time.time()), "robots_allowed": True
                }, links, delay, False)
            except Exception as error:
                status = getattr(error, "code", None)
                if status in (429, 503):
                    rate_limited_count[0] += 1
                is_http_error = isinstance(error, urllib.error.HTTPError)
                if status == 429:
                    backoff = max(DEFAULT_429_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_429_BACKOFF_SECONDS))
                elif status in (500, 502, 503, 504):
                    backoff = max(DEFAULT_5XX_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_5XX_BACKOFF_SECONDS))
                elif not is_http_error and isinstance(error, urllib.error.URLError):
                    backoff = max(DEFAULT_NETWORK_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_NETWORK_BACKOFF_SECONDS))
                elif isinstance(error, TimeoutError):
                    backoff = DEFAULT_NETWORK_BACKOFF_SECONDS
                else:
                    backoff = host_delay(origin(url))
                retryable = status in (429, 500, 502, 503, 504) or (
                    not is_http_error and isinstance(error, urllib.error.URLError)
                ) or isinstance(error, TimeoutError)
                delay = max(host_delay(origin(url)), backoff)
                print(f"skip {url}: {error}; host cooldown {delay:.0f}s")
                return None, None, delay, retryable

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, len(batch))) as pool:
            results = list(pool.map(fetch, batch))

        fetched += len(batch)
        completed_at = time.time()
        for (url, depth), result in zip(batch, results):
            host = host_key(url)
            delay = result[2] if result else host_delay(origin(url))
            host_next_allowed[host] = completed_at + delay
            if result and len(result) > 3 and result[3]:
                queue.append((url, depth))
                transient_retries_queued += 1
            requests_by_host[host] = requests_by_host.get(host, 0) + 1

        for result in results:
            if not result or not result[0]:
                continue
            record, links, _delay, _retryable = result
            records[record["url"]] = record
            depth_counts[str(record["depth"])] = depth_counts.get(str(record["depth"]), 0) + 1
            for candidate in links:
                if candidate not in seen and len(seen) < QUEUE_LIMIT:
                    seen.add(candidate)
                    queue.append((candidate, record["depth"] + 1))

    run_duration = round(time.monotonic() - run_started, 2)
    old = load_json(OUTPUT, {"version": 1, "pages": []})
    merged = {p["url"]: p for p in old.get("pages", []) if p.get("url")}
    # Backfill deterministic lanes for the existing catalog as well as new pages.
    for page in merged.values():
        lane = page.get("lane")
        if not isinstance(lane, str) or lane not in VALID_LANES:
            page["lane"] = classify_lane(page.get("url", ""))
    merged.update(records)
    save_json(OUTPUT, {
        "version": 1, "updated_at": int(time.time()),
        "pages_crawled_this_run": fetched,
        "pages_indexed_this_run": len(records),
        "pages_not_indexed_this_run": fetched - len(records),
        "robots_blocked_this_run": robots_blocked_count[0],
        "robots_policy_failures_this_run": len(robots_policy_failures),
        "robots_policy_failure_hosts_this_run": [
            {"host": host, "reason": reason}
            for host, reason in sorted(robots_policy_failures.items())
        ],
        "rate_limited_responses_this_run": rate_limited_count[0],
        "transient_retries_queued_this_run": transient_retries_queued,
        "sitemap_hosts_checked_this_run": sitemap_hosts_checked,
        "sitemap_fetches_this_run": sitemap_fetches,
        "sitemap_urls_added_this_run": sitemap_urls_added,
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
        "host_next_allowed": host_next_allowed,
        "sitemap_checked": sitemap_checked
    })
    print(f"Fetched {fetched}; indexed {len(records)}; not indexed {fetched - len(records)}; "
          f"robots blocked {robots_blocked_count[0]}; duration {run_duration:.2f}s; "
          f"depth counts {depth_counts}; frontier remaining {len(queue)}; "
          f"discovered {len(seen)} URLs; total index {len(merged)}.")

if __name__ == "__main__":
    main()
