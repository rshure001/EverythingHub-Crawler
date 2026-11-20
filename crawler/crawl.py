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
from safe_http import UnsafeTargetError, build_pinned_opener
from media_discovery import discover_media, merge_media_candidates
from seed_coverage import DEFAULT_SEED_RETRY_SECONDS, schedule_missing_seed_urls
from index_store import load_index, save_index
from catalog_frontier import load_catalog_seed_urls
from product_metadata import extract_product_metadata

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "crawler" / "seeds.txt"
OUTPUT = ROOT / "data" / "index.json"
FRONTIER = ROOT / "data" / "frontier.json"
MEDIA_DISCOVERY = ROOT / "data" / "media-discovery.json"
CATALOG_RECORDS = ROOT / "data" / "universal-catalog-records.json.gz"
MAX_CATALOG_SEEDS_PER_RUN = 20000
WORKERS = 250
QUEUE_LIMIT = 100000
MAX_DEPTH = 3
MAX_FETCH_PER_RUN = 12000
MAX_SITEMAP_HOSTS_PER_RUN = 174
SITEMAP_DISCOVERY_VERSION = 2
MAX_SITEMAP_CHILD_SITEMAPS = 4
MAX_SITEMAP_URLS_PER_HOST = 500
MAX_SITEMAP_BYTES = 2_000_000
SITEMAP_RECHECK_SECONDS = 7 * 24 * 60 * 60
MAX_RETRY_AFTER_SECONDS = 7 * 24 * 60 * 60
SEED_RETRY_SECONDS = DEFAULT_SEED_RETRY_SECONDS
DEFAULT_429_BACKOFF_SECONDS = 3600
DEFAULT_5XX_BACKOFF_SECONDS = 600
DEFAULT_NETWORK_BACKOFF_SECONDS = 60
# Keep index rebuild, validation, and commit inside the 10-minute Actions job timeout.
MAX_RUN_SECONDS = 420
HOST_MIN_INTERVAL = 1.0
ROBOTS_MAX_BYTES = 512_000
TIMEOUT = 15
UA = "EverythingHubCrawler/1.1 (+https://github.com/rshure001/EverythingHub-Crawler)"
ACCESS_CHALLENGE_MAX_TEXT = 1200
PUBLISHED_DATE_META_PRIORITY = {
    "article:published_time": 0,
    "og:published_time": 0,
    "datepublished": 0,
    "dc.date.issued": 1,
    "dcterms.issued": 1,
    "parsely-pub-date": 1,
    "sailthru.date": 1,
    "pubdate": 2,
    "publishdate": 2,
    "publish_date": 2,
    "pub_date": 2,
    "date": 3,
}

ACCESS_CHALLENGE_MARKERS = (
    "powered and protected by privacy",
    "checking your browser before accessing",
    "just a moment...",
    "attention required! | cloudflare",
    "verify you are human",
    "enable javascript and cookies to continue",
    "please enable javascript and cookies to continue",
    "complete the captcha",
    "captcha verification",
    "request blocked by security policy",
    "your request has been blocked",
    "unusual traffic from your network",
    "temporarily blocked",
)


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


def is_access_challenge_page(title, description, text):
    """Identify short anti-bot interstitials without suppressing substantive pages."""
    visible_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(visible_text) > ACCESS_CHALLENGE_MAX_TEXT:
        return False
    combined = " ".join(str(value or "") for value in (title, description, visible_text))
    normalized = re.sub(r"\s+", " ", combined).casefold()
    if any(marker in normalized for marker in ACCESS_CHALLENGE_MARKERS):
        return True
    return (
        "access denied" in normalized
        and len(visible_text) <= 300
        and not str(description or "").strip()
    )


def apply_access_challenge_policy(title, description, text, directives):
    """Keep short challenge stubs out of search and do not expand their page chrome."""
    if not is_access_challenge_page(title, description, text):
        return False
    directives.update(("noindex", "nofollow"))
    return True


def fallback_page_title(url):
    """Build a readable title from the URL when a source omits its page title."""
    raw_url = str(url or "")
    parsed = urllib.parse.urlsplit(raw_url)
    segment = urllib.parse.unquote((parsed.path or "").rstrip("/").rsplit("/", 1)[-1])
    segment = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", segment)
    segment = re.sub(r"[-_]+", " ", segment)
    segment = re.sub(r"\s+", " ", segment).strip(" ._-")
    return segment or parsed.hostname or raw_url or "Untitled page"


def parse_published_at(value):
    """Normalize an explicitly labeled publication date to UTC ISO-8601."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 100:
        return None
    parsed = None
    try:
        if re.fullmatch(r"\d{10,13}", raw):
            number = int(raw)
            seconds = number / 1000 if len(raw) == 13 else number
            parsed = datetime.fromtimestamp(seconds, timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def normalize_text(value, limit):
    """Remove NUL bytes and normalize text before indexing or database writes."""
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", "")).strip()[:limit]


def build_page_record(url, title, description, text, depth, directives, published_at=None,
                      product_metadata=None):
    """Build a page record while retaining crawl depth for noindex pages."""
    if "noindex" in directives:
        # noindex suppresses publication, not link discovery; keep depth so
        # permitted links on this page can still be queued safely.
        return {"url": url, "_eh_noindex": True, "depth": depth}
    display_title = normalize_text(title, 2000) or fallback_page_title(url)
    record = {
        "url": url,
        "title": display_title,
        "lane": classify_lane(url),
        "description": normalize_text(description, 500),
        "text": normalize_text(text, 4000),
        "depth": depth,
        "fetched_at": int(time.time()),
        "robots_allowed": True,
    }
    normalized_published_at = parse_published_at(published_at)
    if normalized_published_at:
        record["published_at"] = normalized_published_at
    if isinstance(product_metadata, dict) and product_metadata.get("type") == "product":
        product = {
            key: normalize_text(product_metadata[key], limit)
            for key, limit in {
                "name": 500, "brand": 160, "sku": 120, "price": 64,
                "currency": 8, "availability": 80, "image": 2048, "url": 2048,
            }.items()
            if isinstance(product_metadata.get(key), (str, int, float))
            and str(product_metadata.get(key)).strip()
        }
        if product.get("name"):
            record["product"] = {"type": "product", **product}
    return record


def enqueue_discovered_links(queue, seen, links, record, queue_limit=QUEUE_LIMIT,
                            indexed_urls=None):
    """Queue unseen links while bounding pending work, not lifetime history."""
    depth = record.get("depth")
    if not isinstance(depth, int) or depth < 0:
        raise ValueError("page record is missing a valid crawl depth")
    indexed = indexed_urls if indexed_urls is not None else set()
    added = 0
    for candidate in links:
        if candidate in seen or candidate in indexed or len(queue) >= queue_limit:
            continue
        seen.add(candidate)
        queue.append((candidate, depth + 1))
        added += 1
    return added


def same_origin(left, right):
    try:
        a = urllib.parse.urlsplit(left)
        b = urllib.parse.urlsplit(right)
        a_port = a.port or (443 if a.scheme.lower() == "https" else 80)
        b_port = b.port or (443 if b.scheme.lower() == "https" else 80)
        return (
            a.scheme.lower() in ("http", "https")
            and a.scheme.lower() == b.scheme.lower()
            and (a.hostname or "").lower() == (b.hostname or "").lower()
            and a_port == b_port
            and a.username is None and a.password is None
            and b.username is None and b.password is None
        )
    except (TypeError, ValueError):
        return False


class RobotsAwareRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow same-origin redirects only after checking robots.txt for the target URL."""
    def __init__(self, is_allowed):
        super().__init__()
        self.is_allowed = is_allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not same_origin(req.full_url, target):
            raise urllib.error.HTTPError(
                req.full_url, code, "cross-origin redirect blocked", headers, fp
            )
        if not self.is_allowed(target):
            raise urllib.error.HTTPError(
                req.full_url, code, "redirect target blocked by robots.txt", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, target)

class RobotsFileRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow bounded RFC 9309 robots.txt redirects, including across authorities."""
    max_redirections = 5
    max_repeats = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        source = urllib.parse.urlsplit(req.full_url)
        destination = urllib.parse.urlsplit(target)
        if (
            destination.scheme.lower() not in ("http", "https")
            or not destination.hostname
            or destination.username is not None
            or destination.password is not None
        ):
            raise urllib.error.HTTPError(
                req.full_url, code, "unsafe robots redirect target", headers, fp
            )
        if source.scheme.lower() == "https" and destination.scheme.lower() != "https":
            raise urllib.error.HTTPError(
                req.full_url, code, "HTTPS robots redirect downgrade blocked", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


class PageParser(HTMLParser):
    """Extract readable text and links while excluding repeated page chrome."""
    def __init__(self):
        super().__init__()
        self.title_on = False
        self.title = []
        self.description = ""
        self.published_at = None
        self._published_at_priority = None
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
            meta_keys = (
                a.get("property", "").strip().lower(),
                meta_name,
                a.get("itemprop", "").strip().lower(),
            )
            priorities = [
                PUBLISHED_DATE_META_PRIORITY[key]
                for key in meta_keys
                if key in PUBLISHED_DATE_META_PRIORITY
            ]
            if priorities:
                priority = min(priorities)
                candidate = parse_published_at(a.get("content") or a.get("datetime"))
                if candidate and (
                    self._published_at_priority is None
                    or priority < self._published_at_priority
                ):
                    self.published_at = candidate
                    self._published_at_priority = priority
        if tag == "time" and a.get("itemprop", "").strip().lower() == "datepublished":
            priority = PUBLISHED_DATE_META_PRIORITY["datepublished"]
            candidate = parse_published_at(a.get("datetime"))
            if candidate and (
                self._published_at_priority is None
                or priority < self._published_at_priority
            ):
                self.published_at = candidate
                self._published_at_priority = priority
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
    try:
        p = urllib.parse.urlsplit(url)
        hostname = p.hostname
    except (TypeError, ValueError):
        return None
    if (
        p.scheme not in ("http", "https")
        or not p.netloc
        or not hostname
        or p.username is not None
        or p.password is not None
    ):
        return None
    # These formats are not parsed as searchable HTML. Reject them before
    # they consume a fetch slot; keep XML because robots-declared sitemaps use it.
    non_html_extensions = {
        ".7z", ".avi", ".bmp", ".css", ".csv", ".doc", ".docx", ".eot",
        ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js", ".m4a", ".m4v",
        ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".odp", ".ods", ".odt",
        ".otf", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".rss", ".svg",
        ".tar", ".tgz", ".tif", ".tiff", ".ttf", ".wav", ".webm", ".webp",
        ".woff", ".woff2", ".xls", ".xlsx", ".zip",
    }
    if Path(urllib.parse.unquote(p.path or "/")).suffix.lower() in non_html_extensions:
        return None
    # Never retain userinfo in crawler URLs; public search links must not carry credentials.
    # Remove fragments and common tracking parameters; retain meaningful query parameters.
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")
             and k.lower() not in ("fbclid", "gclid", "mc_cid", "mc_eid")]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/",
                                    urllib.parse.urlencode(query), ""))

def restore_frontier_queue(items):
    """Restore only canonical searchable-HTML URLs from the persisted frontier."""
    queue = deque()
    for item in items or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        candidate = clean(item["url"])
        if not candidate:
            continue
        try:
            depth = int(item.get("depth", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= depth <= MAX_DEPTH:
            queue.append((candidate, depth))
    return queue


def request(url, is_allowed, max_bytes=2_000_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xml,*/*"
    })
    opener = build_pinned_opener(RobotsAwareRedirectHandler(is_allowed))
    with opener.open(req, timeout=TIMEOUT) as response:
        x_robots_tags = response.headers.get_all("X-Robots-Tag") or []
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeds safe size limit ({max_bytes} bytes)")
        return response.geturl(), response.headers.get_content_type(), x_robots_tags, body

def origin(url):
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def load_robots_policy(site):
    """Fetch robots.txt with a bounded read; deny the host if policy cannot be read."""
    parser = urllib.robotparser.RobotFileParser()
    robots_url = site.rstrip("/") + "/robots.txt"
    parser.set_url(robots_url)
    try:
        req = urllib.request.Request(robots_url, headers={
            "User-Agent": UA, "Accept": "text/plain,*/*"
        })
        # RFC 9309 permits robots.txt redirects across authorities. The rules
        # still apply to the original host; every redirect target remains DNS-pinned
        # to a public address, HTTPS downgrades are rejected, and the chain is bounded.
        opener = build_pinned_opener(RobotsFileRedirectHandler())
        with opener.open(req, timeout=TIMEOUT) as response:
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


def restore_sitemap_checked(frontier):
    """Reset stale sitemap history when discovery semantics have changed."""
    if not isinstance(frontier, dict):
        return {}
    if frontier.get("sitemap_discovery_version") != SITEMAP_DISCOVERY_VERSION:
        return {}
    checked = frontier.get("sitemap_checked", {})
    return checked if isinstance(checked, dict) else {}


def preload_robots_policies(sites, loader=load_robots_policy, workers=WORKERS):
    """Fetch each distinct seed host's robots policy concurrently, fail-closed."""
    unique_sites = list(dict.fromkeys(sites))
    if not unique_sites:
        return {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(int(workers), len(unique_sites)))
    ) as pool:
        parsers = list(pool.map(loader, unique_sites))
    return dict(zip(unique_sites, parsers))


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


def select_batch(queue, is_allowed, host_next_allowed, now, workers=WORKERS,
                 priority_urls=(), in_flight_hosts=()):
    """Select URLs with stable linear-time priority buckets and one URL per host."""
    priority = set(priority_urls or ())
    pending = list(queue)
    queue.clear()
    selected = []
    deferred = []
    selected_hosts = set(in_flight_hosts or ())

    # Preserve the original stable order: seed URLs first, then deeper pages,
    # with original queue order retained inside each priority/depth group.
    bucket_count = 2 * (MAX_DEPTH + 1)
    buckets = [[] for _ in range(bucket_count)]
    for item in pending:
        url, depth = item
        if depth > MAX_DEPTH:
            continue
        priority_group = 0 if url in priority else 1
        depth_group = MAX_DEPTH - depth
        buckets[priority_group * (MAX_DEPTH + 1) + depth_group].append(item)

    for bucket_index, bucket in enumerate(buckets):
        for item_index, (url, depth) in enumerate(bucket):
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
                deferred.extend(bucket[item_index + 1:])
                for remaining_bucket in buckets[bucket_index + 1:]:
                    deferred.extend(remaining_bucket)
                queue.extend(deferred)
                return selected

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
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(path)


def merge_index_records(existing_pages, new_records, noindex_urls=()):
    """Merge new pages while removing only URLs explicitly marked noindex."""
    blocked = set(noindex_urls)
    merged = {
        page["url"]: page
        for page in existing_pages
        if (
            isinstance(page, dict)
            and page.get("url")
            and page["url"] not in blocked
            and not is_access_challenge_page(
                page.get("title"), page.get("description"), page.get("text")
            )
        )
    }
    for page in merged.values():
        if not str(page.get("title") or "").strip():
            page["title"] = fallback_page_title(page["url"])
        lane = page.get("lane")
        if not isinstance(lane, str) or lane not in VALID_LANES:
            page["lane"] = classify_lane(page.get("url", ""))
    for url, page in new_records.items():
        if url in blocked:
            continue
        previous = merged.get(url, {})
        if not page.get("published_at") and previous.get("published_at"):
            # Preserve previously captured publication metadata when a source
            # temporarily omits its date tag on a later crawl.
            page["published_at"] = previous["published_at"]
        merged[url] = page
    return merged


def summarize_fetch_outcomes(outcomes):
    """Separate HTTP success, HTML parsing, non-HTML responses, and errors."""
    return {
        "fetch_attempts": len(outcomes),
        "successful_http_responses": sum(1 for item in outcomes if item.get("http_success")),
        "html_pages_parsed": sum(1 for item in outcomes if item.get("html_parsed")),
        "non_html_responses": sum(1 for item in outcomes if item.get("non_html")),
        "fetch_or_parse_errors": sum(1 for item in outcomes if item.get("failed")),
    }


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
    access_challenge_pages_count = [0]
    media_candidates_this_run = []
    media_robots_blocked_count = 0
    media_external_deferred_count = 0

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

    # Resume the durable frontier and retry missing configured seed hosts on a
    # long cooldown. A seed that was seen but blocked, noindexed, or permanently
    # failed must not disappear from coverage forever.
    old_index = load_index(OUTPUT, {"version": 1, "pages": []})
    old_pages = old_index.get("pages", []) if isinstance(old_index, dict) else []
    indexed_urls = {
        page.get("url")
        for page in old_pages
        if isinstance(page, dict) and isinstance(page.get("url"), str)
    }
    indexed_hosts = {host_key(url) for url in indexed_urls}

    frontier = load_json(FRONTIER, {"queue": [], "seen": []})
    # "seen" is a deduplication history, not the pending-queue budget.
    # Drop already-indexed and now-invalid entries so the history does not
    # permanently fill the queue's capacity.
    seen = {
        candidate
        for raw in frontier.get("seen", [])
        if (candidate := clean(raw)) and candidate not in indexed_urls
    }
    seed_retry_after = frontier.get("seed_retry_after", {})
    if not isinstance(seed_retry_after, dict):
        seed_retry_after = {}
    saved_host_schedule = frontier.get("host_next_allowed", {})
    if not isinstance(saved_host_schedule, dict):
        saved_host_schedule = {}
    # Recheck all seed-host sitemaps once after fixing the lifetime-seen
    # ceiling; the previous run marked many sitemaps checked without enqueueing
    # their URLs because the old seen set was full.
    sitemap_checked = restore_sitemap_checked(frontier)
    host_next_allowed = {}
    for saved_host, timestamp in saved_host_schedule.items():
        if not isinstance(timestamp, (int, float)):
            continue
        key = str(saved_host).lower()
        if "://" in key:
            parsed = urllib.parse.urlsplit(key)
            key = (parsed.hostname or parsed.netloc).lower()
        host_next_allowed[key] = max(host_next_allowed.get(key, 0), float(timestamp))
    # The saved frontier predates current URL filters. Re-clean it on
    # resume so old PDFs, images, scripts, and malformed URLs never consume
    # another network request.
    queue = deque(
        (url, depth)
        for url, depth in restore_frontier_queue(frontier.get("queue", []))
        if url not in indexed_urls
    )
    seen.update(url for url, _depth in queue)
    # Feed normalized dataset landing pages into the durable HTML frontier.
    # Bulk CSV/JSON/media resource URLs remain metadata, not HTML crawl targets.
    catalog_seed_urls = load_catalog_seed_urls(
        CATALOG_RECORDS, limit=MAX_CATALOG_SEEDS_PER_RUN
    )
    queued_urls = {url for url, _depth in queue}
    catalog_seed_urls_added = 0
    for raw_url in catalog_seed_urls:
        candidate = clean(raw_url)
        if (
            candidate
            and candidate not in indexed_urls
            and candidate not in seen
            and candidate not in queued_urls
            and len(queue) < QUEUE_LIMIT
        ):
            queue.append((candidate, 0))
            seen.add(candidate)
            queued_urls.add(candidate)
            catalog_seed_urls_added += 1
    for seed in seeds:
        if seed not in seen and seed not in indexed_urls and len(queue) < QUEUE_LIMIT:
            seen.add(seed)

    # Do not spend requests on a seed URL when that host already has indexed
    # coverage. Missing hosts are re-queued at most once per 24 hours; normal
    # robots checks and per-host rate limits still run in select_batch().
    seed_set = set(seeds)
    queue = deque(
        (url, depth) for url, depth in queue
        if not (url in seed_set and host_key(url) in indexed_hosts)
    )
    queued_urls = {url for url, _depth in queue}
    eligible_seeds = [seed for seed in seeds if seed in seen]
    for seed_url, depth in schedule_missing_seed_urls(
        eligible_seeds,
        indexed_hosts,
        queued_urls,
        seed_retry_after,
        now=time.time(),
        cooldown_seconds=SEED_RETRY_SECONDS,
        queue_limit=QUEUE_LIMIT,
        host_key=host_key,
    ):
        queue.append((seed_url, depth))
        seen.add(seed_url)

    # Sitemap discovery is deliberately separate from the page-fetch budget:
    # use only robots.txt declarations, stay on the same host, and cap every run.
    run_started = time.monotonic()
    # Loading robots.txt one host at a time serialized the crawl startup.
    # Preload one policy request per distinct seed origin concurrently; all
    # later URL checks reuse these cached policies and remain robots-paced.
    robots.update(preload_robots_policies(origin(seed) for seed in seeds))
    for site, parser in robots.items():
        failure = getattr(parser, "everythinghub_policy_error", None)
        if failure:
            robots_policy_failures[host_key(site)] = failure

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

    def discover_sitemaps_for_host(item):
        host, site = item
        checked_now = int(time.time())
        parser = policy(site)
        if getattr(parser, "disallow_all", False):
            # Retry a robots.txt read failure after one hour, not every run.
            retry_at = checked_now - SITEMAP_RECHECK_SECONDS + 3600
            return host, [], 1, 0, 0, retry_at, None

        declared = parser.site_maps() or []
        sitemap_url = None
        site_netloc = urllib.parse.urlsplit(site).netloc.lower()
        for raw_url in declared:
            candidate = clean(raw_url)
            if candidate and urllib.parse.urlsplit(candidate).netloc.lower() == site_netloc:
                sitemap_url = candidate
                break
        if not sitemap_url:
            return host, [], 1, 0, 0, checked_now, None

        pending_sitemaps = [sitemap_url]
        discovered_pages = []
        sitemap_index = 0
        fetches = 0
        rate_limited = 0
        checked_at = checked_now
        next_allowed = float(host_next_allowed.get(host, 0) or 0)

        while sitemap_index < len(pending_sitemaps) and sitemap_index <= MAX_SITEMAP_CHILD_SITEMAPS:
            current_sitemap = pending_sitemaps[sitemap_index]
            sitemap_index += 1
            wait = max(0.0, next_allowed - time.time())
            remaining = MAX_RUN_SECONDS - (time.monotonic() - run_started)
            if wait > remaining:
                break
            if wait > 0:
                time.sleep(wait)
            if not allowed(current_sitemap):
                continue

            fetches += 1
            try:
                final_sitemap, _content_type, _x_robots_tags, body = request(
                    current_sitemap, allowed, max_bytes=MAX_SITEMAP_BYTES + 1
                )
            except Exception as error:
                status = getattr(error, "code", None)
                if status in (429, 503):
                    rate_limited += 1
                if status == 429:
                    backoff = max(DEFAULT_429_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_429_BACKOFF_SECONDS))
                elif status in (500, 502, 503, 504):
                    backoff = max(DEFAULT_5XX_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_5XX_BACKOFF_SECONDS))
                elif not isinstance(error, urllib.error.HTTPError) and isinstance(error, urllib.error.URLError):
                    backoff = max(DEFAULT_NETWORK_BACKOFF_SECONDS, retry_after_seconds(error, DEFAULT_NETWORK_BACKOFF_SECONDS))
                else:
                    backoff = host_delay(origin(current_sitemap))
                next_allowed = time.time() + max(host_delay(origin(current_sitemap)), backoff)
                if status == 403:
                    cooldown = SITEMAP_RECHECK_SECONDS
                else:
                    cooldown = max(DEFAULT_NETWORK_BACKOFF_SECONDS, backoff)
                checked_at = int(
                    time.time() - SITEMAP_RECHECK_SECONDS + min(cooldown, MAX_RETRY_AFTER_SECONDS)
                )
                print(f"skip sitemap {current_sitemap}: {error}; host cooldown {backoff:.0f}s")
                if status in (403, 429, 500, 502, 503, 504) or (
                    not isinstance(error, urllib.error.HTTPError) and isinstance(error, urllib.error.URLError)
                ):
                    break
                continue

            next_allowed = time.time() + max(
                host_delay(origin(current_sitemap)),
                host_delay(origin(final_sitemap)),
            )
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

        return (
            host,
            list(dict.fromkeys(discovered_pages))[:MAX_SITEMAP_URLS_PER_HOST],
            1,
            fetches,
            rate_limited,
            checked_at,
            next_allowed if next_allowed > 0 else None,
        )

    # Discover across the whole configured seed network concurrently. Each
    # host gets its own robots policy and its own politeness clock; no host is
    # allowed to exceed the robots-derived request rate.
    if candidate_hosts:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(WORKERS, len(candidate_hosts))
        ) as sitemap_pool:
            sitemap_results = list(sitemap_pool.map(
                discover_sitemaps_for_host, candidate_hosts
            ))
    else:
        sitemap_results = []

    for host, discovered_pages, checked_count, fetch_count, rate_count, checked_at, next_allowed in sitemap_results:
        sitemap_hosts_checked += checked_count
        sitemap_fetches += fetch_count
        rate_limited_count[0] += rate_count
        sitemap_checked[host] = checked_at
        if next_allowed is not None:
            host_next_allowed[host] = next_allowed
        for candidate in discovered_pages:
            if candidate not in seen and candidate not in indexed_urls and len(queue) < QUEUE_LIMIT:
                seen.add(candidate)
                queue.append((candidate, 1))
                sitemap_urls_added += 1

    records = {}
    noindex_urls = set()
    fetched = 0
    depth_counts = {}
    requests_by_host = {}
    fetch_outcomes = []

    def fetch(item):
        url, depth = item
        http_success = False
        html_parsed = False
        non_html = False
        try:
            final, content_type, x_robots_tags, body = request(url, allowed)
            http_success = True
            delay = max(host_delay(origin(url)), host_delay(origin(final)))
            if content_type != "text/html":
                non_html = True
                outcome = {
                    "http_success": True, "html_parsed": False,
                    "non_html": True, "failed": False,
                }
                return None, None, delay, False, outcome
            html = body.decode("utf-8", "replace")
            parser = PageParser()
            parser.feed(html)
            html_parsed = True
            directives = set(parser.robots_directives)
            directives.update(parse_indexing_directives(x_robots_tags))
            if "nofollow" in directives:
                nofollow_pages_count[0] += 1
            title = normalize_text(" ".join(parser.title), 2000)
            text = normalize_text(" ".join(parser.text), 4000)
            description = normalize_text(parser.description, 500)
            if apply_access_challenge_policy(title, description, text, directives):
                access_challenge_pages_count[0] += 1
            page_media = []
            page_media_robots_blocked = 0
            page_media_external_deferred = 0
            if "noindex" not in directives and "nofollow" not in directives:
                for candidate in discover_media(html, final, title, description):
                    if not same_origin(final, candidate["url"]):
                        page_media_external_deferred += 1
                        continue
                    if robots_can_fetch(policy(origin(final)), UA, candidate["url"]):
                        candidate["robots_allowed"] = True
                        candidate["robots_checked_at"] = int(time.time())
                        page_media.append(candidate)
                    else:
                        page_media_robots_blocked += 1
            text, description = apply_snippet_policy(text, description, directives)
            product_metadata = {}
            if "noindex" not in directives:
                try:
                    product_metadata = extract_product_metadata(html, final)
                except (ValueError, TypeError, RecursionError):
                    # Product metadata is additive; malformed structured data
                    # must never make an otherwise valid page fail to index.
                    product_metadata = {}
            record = build_page_record(
                final, title, description, text, depth, directives,
                parser.published_at, product_metadata,
            )
            links = []
            if depth < MAX_DEPTH and "nofollow" not in directives:
                host = urllib.parse.urlsplit(final).netloc
                for href in parser.links:
                    candidate = clean(urllib.parse.urljoin(final, href))
                    if candidate and urllib.parse.urlsplit(candidate).netloc == host:
                        links.append(candidate)
            outcome = {
                "http_success": True, "html_parsed": True,
                "non_html": False, "failed": False,
                "media_candidates": page_media,
                "media_robots_blocked": page_media_robots_blocked,
                "media_external_deferred": page_media_external_deferred,
            }
            return record, links, delay, False, outcome
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
            outcome = {
                "http_success": http_success, "html_parsed": html_parsed,
                "non_html": non_html, "failed": True,
            }
            return None, None, delay, retryable, outcome



    # Keep the worker pool full as individual hosts finish instead of waiting
    # for the slowest response in a 150-request wave. A host remains reserved
    # while its request is in flight, then its robots-derived delay starts at
    # completion, preserving the existing per-host politeness guarantees.
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        in_flight = {}
        while queue or in_flight:
            elapsed = time.monotonic() - run_started
            may_submit = elapsed < MAX_RUN_SECONDS and fetched < MAX_FETCH_PER_RUN

            if may_submit and len(in_flight) < WORKERS and queue:
                available_slots = min(
                    WORKERS - len(in_flight),
                    MAX_FETCH_PER_RUN - fetched,
                )
                batch = select_batch(
                    queue, allowed, host_next_allowed, time.time(),
                    workers=available_slots, priority_urls=seeds,
                    in_flight_hosts={host_key(item[0]) for item in in_flight.values()},
                )
                for item in batch:
                    future = pool.submit(fetch, item)
                    in_flight[future] = item
                    fetched += 1

            if not in_flight:
                if not queue or fetched >= MAX_FETCH_PER_RUN:
                    break
                remaining = MAX_RUN_SECONDS - (time.monotonic() - run_started)
                if remaining <= 0:
                    break
                wait = next_host_wait(queue, host_next_allowed, time.time())
                if wait > remaining:
                    break
                if wait > 0:
                    time.sleep(wait)
                continue

            remaining = MAX_RUN_SECONDS - (time.monotonic() - run_started)
            completed, _pending = concurrent.futures.wait(
                in_flight,
                timeout=max(0.0, remaining) if remaining > 0 else None,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not completed:
                # The run budget has elapsed. Drain already-started requests,
                # but do not submit additional work beyond the budget.
                continue

            for future in completed:
                url, depth = in_flight.pop(future)
                result = future.result()
                completed_at = time.time()
                host = host_key(url)
                delay = result[2] if result else host_delay(origin(url))
                host_next_allowed[host] = completed_at + delay

                if result and len(result) > 3 and result[3]:
                    queue.append((url, depth))
                    transient_retries_queued += 1
                requests_by_host[host] = requests_by_host.get(host, 0) + 1
                if result and len(result) > 4 and isinstance(result[4], dict):
                    outcome = result[4]
                    fetch_outcomes.append(outcome)
                    media_candidates_this_run.extend(outcome.get("media_candidates", []))
                    media_robots_blocked_count += int(outcome.get("media_robots_blocked", 0) or 0)
                    media_external_deferred_count += int(outcome.get("media_external_deferred", 0) or 0)

                if not result or not result[0]:
                    continue
                record, links, _delay, _retryable, _outcome = result
                if record.get("_eh_noindex"):
                    index_policy_blocked_count[0] += 1
                    noindex_urls.add(record["url"])
                else:
                    records[record["url"]] = record
                    indexed_urls.add(record["url"])
                    seen.discard(record["url"])
                    depth_counts[str(record["depth"])] = depth_counts.get(str(record["depth"]), 0) + 1
                enqueue_discovered_links(
                    queue, seen, links, record, indexed_urls=indexed_urls
                )

    run_duration = round(time.monotonic() - run_started, 2)
    fetch_metrics = summarize_fetch_outcomes(fetch_outcomes)
    old = old_index
    old_challenge_urls = {
        page.get("url")
        for page in old.get("pages", [])
        if isinstance(page, dict)
        and isinstance(page.get("url"), str)
        and is_access_challenge_page(
            page.get("title"), page.get("description"), page.get("text")
        )
    }
    removed_existing_challenge_count = len(
        old_challenge_urls - set(records) - noindex_urls
    )
    merged = merge_index_records(old.get("pages", []), records, noindex_urls)
    media_old = load_json(MEDIA_DISCOVERY, {"version": 1, "candidates": []})
    media_existing = media_old.get("candidates", []) if isinstance(media_old, dict) else []
    updated_at = int(time.time())
    media_merged, media_added = merge_media_candidates(
        media_existing if isinstance(media_existing, list) else [],
        media_candidates_this_run, updated_at,
    )
    save_json(MEDIA_DISCOVERY, {
        "version": 1, "updated_at": updated_at,
        "total_candidates": len(media_merged), "candidates": media_merged,
    })
    save_index(OUTPUT, {
        "version": 1, "updated_at": updated_at,
        "pages_crawled_this_run": fetched,
        "fetch_attempts_this_run": fetch_metrics["fetch_attempts"],
        "successful_http_responses_this_run": fetch_metrics["successful_http_responses"],
        "html_pages_parsed_this_run": fetch_metrics["html_pages_parsed"],
        "non_html_responses_this_run": fetch_metrics["non_html_responses"],
        "fetch_or_parse_errors_this_run": fetch_metrics["fetch_or_parse_errors"],
        "pages_indexed_this_run": len(records),
        "pages_not_indexed_this_run": fetched - len(records),
        "robots_blocked_this_run": robots_blocked_count[0],
        "index_policy_blocked_this_run": index_policy_blocked_count[0],
        "access_challenge_pages_this_run": access_challenge_pages_count[0],
        "access_challenge_pages_removed_from_existing_index": removed_existing_challenge_count,
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
        "media_candidates_discovered_this_run": len(media_candidates_this_run),
        "media_candidates_added_this_run": media_added,
        "media_candidates_robots_blocked_this_run": media_robots_blocked_count,
        "media_external_candidates_deferred_this_run": media_external_deferred_count,
        "media_candidates_total": len(media_merged),
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
        "sitemap_checked": sitemap_checked,
        "sitemap_discovery_version": SITEMAP_DISCOVERY_VERSION,
        "seed_retry_after": seed_retry_after
    })
    print(f"Fetched {fetched}; indexed {len(records)}; not indexed {fetched - len(records)}; "
          f"robots blocked {robots_blocked_count[0]}; access challenges excluded {access_challenge_pages_count[0]}; "
          f"duration {run_duration:.2f}s; "
          f"depth counts {depth_counts}; frontier remaining {len(queue)}; "
          f"discovered {len(seen)} URLs; catalog landing pages queued {catalog_seed_urls_added}; "
          f"total index {len(merged)}.")

if __name__ == "__main__":
    main()
