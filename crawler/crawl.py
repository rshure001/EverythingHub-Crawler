#!/usr/bin/env python3
"""Polite, resumable, depth-limited crawler for Everything Hub."""
import concurrent.futures
import email.utils
import json
import math
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


def parse_indexing_directives(values, useragent=UA):
    """Return applicable indexing and snippet directives from meta or X-Robots-Tag."""
    product = useragent.split("/", 1)[0].split()[0].lower()
    non_agent_prefixes = {
        "max-snippet", "max-image-preview", "max-video-preview",
        "unavailable_after", "notranslate",
    }
    directives = set()
    for value in values or []:
        raw = str(value or "").strip()
        if not raw:
            continue
        match = re.match(r"^([a-z0-9._*-]+)\s*:\s*(.*)$", raw, re.IGNORECASE)
        if match and match.group(1).lower() not in non_agent_prefixes:
            agent = match.group(1).lower()
            if agent not in ("*", product) and agent not in product:
                continue
            raw = match.group(2)
        directives.update(re.findall(
            r"(?<![a-z0-9_-])(noindex|nofollow|nosnippet|none)(?![a-z0-9_-])",
            raw.lower(),
        ))
        directives.update(
            "max-snippet:" + str(int(limit))
            for limit in re.findall(
                r"(?<![a-z0-9_-])max-snippet\s*:\s*(-?\d+)",
                raw.lower(),
            )
        )
    if "none" in directives:
        directives.update(("noindex", "nofollow"))
    return directives


def apply_snippet_policy(text, description, directives):
    """Apply nosnippet and the strictest applicable max-snippet character limit."""
    if "nosnippet" in directives:
        return "", ""
    limits = []
    for directive in directives:
        match = re.fullmatch(r"max-snippet:(-?\d+)", str(directive))
        if match:
            value = int(match.group(1))
            if value >= 0:
                limits.append(value)
    if not limits:
        return text, description
    limit = min(limits)
    return text[:limit], description[:limit]


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
        self.robots_directives = set()
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = dict(attrs)
        if tag == "title":
            self.title_on = True
        if tag == "meta":
            meta_name = a.get("name", "").strip().lower()
            if meta_name == "description":
                self.description = a.get("content", "")
            elif meta_name in ("robots", UA.split("/", 1)[0].lower()):
                self.robots_directives.update(parse_indexing_directives([a.get("content", "")]))
        if tag == "a" and a.get("href"):
            rel = {item.lower() for item in a.get("rel", "").split()}
            if "nofollow" not in rel:
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
        x_robots_tags = response.headers.get_all("X-Robots-Tag") or []
        return response.geturl(), response.headers.get_content_type(), x_robots_tags, response.read(max_bytes)

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
    """Parse robots groups and retain path plus per-host rate directives."""
    groups = []
    agents = []
    rules = []
    crawl_delays = []
    request_rates = []

    def flush():
        if agents:
            groups.append({
                "agents": list(agents),
                "rules": list(rules),
                "crawl_delays": list(crawl_delays),
                "request_rates": list(request_rates),
            })

    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            if agents:
                flush()
                agents = []
                rules = []
                crawl_delays = []
                request_rates = []
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules or crawl_delays or request_rates:
                flush()
                agents = []
                rules = []
                crawl_delays = []
                request_rates = []
            if value:
                agents.append(value.lower())
        elif key in ("allow", "disallow") and agents:
            if key == "disallow" and not value:
                continue
            if value:
                rules.append({"pattern": value, "allow": key == "allow"})
        elif key == "crawl-delay" and agents:
            try:
                delay = float(value)
                if math.isfinite(delay) and delay >= 0:
                    crawl_delays.append(delay)
            except (TypeError, ValueError):
                continue
        elif key == "request-rate" and agents:
            parts = value.split("/", 1)
            if len(parts) == 2:
                try:
                    requests = int(parts[0].strip())
                    seconds = float(parts[1].strip())
                    if requests > 0 and math.isfinite(seconds) and seconds > 0:
                        request_rates.append((requests, seconds))
                except (TypeError, ValueError):
                    continue
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


def matching_robots_groups(groups, useragent):
    """Return all groups for the most-specific matching user-agent product token."""
    product_parts = useragent.split("/", 1)[0].split()
    product = product_parts[0].lower() if product_parts else ""
    matches = []
    for group in groups:
        scores = [
            len(agent)
            for agent in group["agents"]
            if agent == "*" or (agent and agent in product)
        ]
        if scores:
            matches.append((max(scores), group))

    specific = [item for item in matches if item[0] > 0]
    if specific:
        best_score = max(score for score, _group in specific)
        return [group for score, group in specific if score == best_score]
    return [group for score, group in matches if score == 0]


def robots_can_fetch(parser, useragent, url):
    """Apply the most-specific matching robots rule; Allow wins equal-specificity ties."""
    if parser.disallow_all:
        return False
    if parser.allow_all:
        return True

    groups = getattr(parser, "everythinghub_groups", None)
    if groups is None:
        return parser.can_fetch(useragent, url)

    selected = matching_robots_groups(groups, useragent)
    if not selected:
        return True

    parsed = urllib.parse.urlsplit(url)
    target = _robots_normalize_component(
        parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"
    )
    if parsed.query:
        target += "?" + _robots_normalize_component(
            parsed.query, safe="/?%:@!$&'()*+,;=-._~"
        )

    matches = []
    for group in selected:
        for rule in group["rules"]:
            pattern = rule["pattern"]
            anchored = pattern.endswith("$")
            raw_pattern = pattern[:-1] if anchored else pattern
            normalized = _robots_normalize_component(
                raw_pattern, safe="/%:@!$&'()*+,;=-._~"
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
    """Return the strictest matching robots rate directive, with a safe minimum."""
    groups = getattr(parser, "everythinghub_groups", None)
    if groups is None:
        crawl_delay = parser.crawl_delay(UA) or 0
        request_rate = parser.request_rate(UA)
        rate_delay = (
            request_rate.seconds / request_rate.requests
            if request_rate and request_rate.requests
            else 0
        )
        return max(HOST_MIN_INTERVAL, float(crawl_delay), float(rate_delay))

    selected = matching_robots_groups(groups, UA)
    delays = [
        delay
        for group in selected
        for delay in group.get("crawl_delays", [])
    ]
    rate_delays = [
        seconds / requests
        for group in selected
        for requests, seconds in group.get("request_rates", [])
    ]
    return max([HOST_MIN_INTERVAL, *delays, *rate_delays])


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


def merge_index_records(existing_pages, new_records, noindex_urls=()):
    """Merge new pages while removing only URLs explicitly marked noindex."""
    blocked = set(noindex_urls)
    merged = {
        page["url"]: page
        for page in existing_pages
        if isinstance(page, dict) and page.get("url") and page["url"] not in blocked
    }
    for page in merged.values():
        lane = page.get("lane")
        if not isinstance(lane, str) or lane not in VALID_LANES:
            page["lane"] = classify_lane(page.get("url", ""))
    merged.update({
        url: page for url, page in new_records.items()
        if url not in blocked
    })
    return merged


def main():
    seeds = [clean(x.strip()) for x in SEEDS.read_text(encoding="utf-8").splitlines()
             if x.strip() and not x.startswith("#")]
    seeds = list(dict.fromkeys(x for x in seeds if x))
    robots = {}
    robots_policy_failures = {}
    robots_blocked_count = [0]
    index_policy_blocked_count = [0]
    nofollow_pages_count = [0]
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
                final_sitemap, _content_type, _x_robots_tags, body = request(
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
    noindex_urls = set()
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
                final, content_type, x_robots_tags, body = request(url, allowed)
                delay = max(host_delay(origin(url)), host_delay(origin(final)))
                if content_type != "text/html":
                    return None, None, delay, False
                parser = PageParser()
                parser.feed(body.decode("utf-8", "replace"))
                directives = set(parser.robots_directives)
                directives.update(parse_indexing_directives(x_robots_tags))
                if "nofollow" in directives:
                    nofollow_pages_count[0] += 1
                title = re.sub(r"\s+", " ", " ".join(parser.title)).strip()
                text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()[:4000]
                description = parser.description[:500]
                text, description = apply_snippet_policy(text, description, directives)
                if "noindex" in directives:
                    record = {"url": final, "_eh_noindex": True}
                else:
                    record = {
                        "url": final, "title": title, "lane": classify_lane(final),
                        "description": description,
                        "text": text, "depth": depth, "fetched_at": int(time.time()), "robots_allowed": True
                    }
                links = []
                if depth < MAX_DEPTH and "nofollow" not in directives:
                    host = urllib.parse.urlsplit(final).netloc
                    for href in parser.links:
                        candidate = clean(urllib.parse.urljoin(final, href))
                        if candidate and urllib.parse.urlsplit(candidate).netloc == host:
                            links.append(candidate)
                return record, links, delay, False
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
            if record.get("_eh_noindex"):
                index_policy_blocked_count[0] += 1
                noindex_urls.add(record["url"])
            else:
                records[record["url"]] = record
                depth_counts[str(record["depth"])] = depth_counts.get(str(record["depth"]), 0) + 1
            for candidate in links:
                if candidate not in seen and len(seen) < QUEUE_LIMIT:
                    seen.add(candidate)
                    queue.append((candidate, record["depth"] + 1))

    run_duration = round(time.monotonic() - run_started, 2)
    old = load_json(OUTPUT, {"version": 1, "pages": []})
    merged = merge_index_records(old.get("pages", []), records, noindex_urls)
    save_json(OUTPUT, {
        "version": 1, "updated_at": int(time.time()),
        "pages_crawled_this_run": fetched,
        "pages_indexed_this_run": len(records),
        "pages_not_indexed_this_run": fetched - len(records),
        "robots_blocked_this_run": robots_blocked_count[0],
        "index_policy_blocked_this_run": index_policy_blocked_count[0],
        "nofollow_pages_this_run": nofollow_pages_count[0],
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
