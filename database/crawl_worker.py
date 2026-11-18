#!/usr/bin/env python3
"""Conservative database-native crawler locked to the Everything Hub eh_quad schema."""

import hashlib
import ipaddress
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crawler"))
import crawl as public_crawler
from lane_classifier import VALID_LANES, classify_lane

SCHEMA = "eh_quad"
USER_AGENT = public_crawler.UA
LOCK_KEY = 0x454851554144
REQUEST_TIMEOUT = 15
ROBOTS_MAX_BYTES = public_crawler.ROBOTS_MAX_BYTES
PAGE_MAX_BYTES = 2_000_000
ROBOTS_TTL_SECONDS = 3600
ROBOTS_FAILURE_RETRY_SECONDS = 3600
DEFAULT_429_BACKOFF_SECONDS = 3600
DEFAULT_5XX_BACKOFF_SECONDS = 600
DEFAULT_NETWORK_BACKOFF_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = public_crawler.MAX_RETRY_AFTER_SECONDS
STALE_CLAIM_SECONDS = 15 * 60
MAX_RUN_SECONDS = 360
MAX_PAGES_HARD_CAP = 100
MAX_ITEMS_HARD_CAP = 300
MAX_DEPTH_HARD_CAP = 3
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
SKIP_TEXT_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}


class CrossOriginRedirectError(RuntimeError):
    pass


class RobotsDeniedRedirectError(RuntimeError):
    pass


class RobotsUnavailable(RuntimeError):
    def __init__(self, message, retry_after=ROBOTS_FAILURE_RETRY_SECONDS):
        super().__init__(message)
        self.retry_after = min(max(1.0, float(retry_after)), MAX_RETRY_AFTER_SECONDS)


def same_origin(left, right):
    try:
        a = urllib.parse.urlsplit(left)
        b = urllib.parse.urlsplit(right)
        a_port = a.port or (443 if a.scheme.lower() == "https" else 80)
        b_port = b.port or (443 if b.scheme.lower() == "https" else 80)
        return (
            a.scheme.lower() == b.scheme.lower()
            and (a.hostname or "").lower() == (b.hostname or "").lower()
            and a_port == b_port
            and a.username is None and a.password is None
            and b.username is None and b.password is None
        )
    except (TypeError, ValueError):
        return False


def clean_url(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if any(ch.isspace() or ord(ch) < 0x20 for ch in value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return public_crawler.clean(value)


def is_safe_target(url, test_mode=False):
    """Reject private, loopback, link-local, and unresolved destinations."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if not host:
            return False
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ]
        if not addresses:
            return False
        for address in addresses:
            if test_mode and address.is_loopback:
                continue
            if not address.is_global:
                return False
        return True
    except (OSError, TypeError, ValueError):
        return False


def validate_schema(value=None):
    requested = value if value is not None else os.environ.get("EH_DB_SCHEMA", SCHEMA)
    if requested != SCHEMA:
        raise ValueError("The database crawler is locked to eh_quad; no other schema is permitted.")
    return SCHEMA


def bounded_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def retry_after_seconds(error, default_seconds=0):
    headers = getattr(error, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    seconds = None
    if raw:
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(raw))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - public_crawler.datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = None
    if seconds is None:
        seconds = default_seconds
    return min(max(0.0, float(seconds)), float(MAX_RETRY_AFTER_SECONDS))


class SameOriginRobotsRedirectHandler(public_crawler.RobotsAwareRedirectHandler):
    """Reject cross-origin redirects and re-check robots.txt before following redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not same_origin(req.full_url, target):
            raise CrossOriginRedirectError("cross-origin redirect rejected")
        if not self.is_allowed(target):
            raise RobotsDeniedRedirectError("redirect target disallowed by robots.txt")
        return super().redirect_request(req, fp, code, msg, headers, target)


class MediaPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.description = ""
        self.text_parts = []
        self.links = []
        self.media = []
        self.directives = set()
        self.in_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            name = str(attrs.get("name", "")).strip().lower()
            if name == "description":
                self.description = str(attrs.get("content", ""))
            elif name in ("robots", USER_AGENT.split("/", 1)[0].lower(), "*"):
                self.directives.update(public_crawler.parse_indexing_directives([attrs.get("content", "")]))
        if tag == "a" and attrs.get("href"):
            rel = {part.lower() for part in str(attrs.get("rel", "")).split()}
            if "nofollow" not in rel:
                self.links.append(attrs["href"])
        if tag in ("img", "video", "audio", "source") and attrs.get("src"):
            media_type = "image" if tag == "img" else "video" if tag == "video" else "audio" if tag == "audio" else "media"
            declared = str(attrs.get("type", "")).lower()
            if declared.startswith("image/"):
                media_type = "image"
            elif declared.startswith("video/"):
                media_type = "video"
            elif declared.startswith("audio/"):
                media_type = "audio"
            self.media.append((attrs["src"], media_type, str(attrs.get("alt", ""))[:500]))
        if tag in SKIP_TEXT_TAGS:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in SKIP_TEXT_TAGS and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if not self.skip_depth and data.strip():
            self.text_parts.append(data.strip())


def parse_robots_policy(status, body):
    parser = urllib.robotparser.RobotFileParser()
    if status == 404:
        parser.parse([])
        parser.allow_all = True
        parser.everythinghub_groups = []
        return parser
    if status < 200 or status >= 300:
        raise RobotsUnavailable(f"robots.txt returned HTTP {status}")
    if len(body) > ROBOTS_MAX_BYTES:
        raise RobotsUnavailable("robots.txt exceeds the safe size limit")
    text = body.decode("utf-8", "replace")
    parser.parse(text.splitlines())
    parser.everythinghub_groups = public_crawler.parse_robots_groups(text)
    return parser


def _pace(url, delay, last_request):
    host = public_crawler.host_key(url)
    wait = max(0.0, last_request.get(host, 0.0) + delay - time.monotonic())
    if wait:
        time.sleep(wait)
    last_request[host] = time.monotonic()


def load_robots_policy(site, last_request, minimum_delay):
    robots_url = site.rstrip("/") + "/robots.txt"
    opener = urllib.request.build_opener(
        SameOriginRobotsRedirectHandler(lambda target: same_origin(robots_url, target))
    )
    _pace(robots_url, minimum_delay, last_request)
    request = urllib.request.Request(robots_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/plain,*/*",
    })
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(ROBOTS_MAX_BYTES + 1)
            return parse_robots_policy(response.status, body)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return parse_robots_policy(404, b"")
        default = DEFAULT_429_BACKOFF_SECONDS if error.code == 429 else DEFAULT_5XX_BACKOFF_SECONDS
        retry = retry_after_seconds(error, default) if error.code in (429, 500, 502, 503, 504) else ROBOTS_FAILURE_RETRY_SECONDS
        raise RobotsUnavailable(f"robots.txt returned HTTP {error.code}", retry) from error
    except CrossOriginRedirectError as error:
        raise RobotsUnavailable(str(error), ROBOTS_FAILURE_RETRY_SECONDS) from error
    except RobotsUnavailable:
        raise
    except Exception as error:
        raise RobotsUnavailable(f"robots.txt unavailable: {type(error).__name__}") from error


def get_robots_policy(site, cache, last_request, minimum_delay):
    cached = cache.get(site)
    now = time.monotonic()
    if cached and now - cached[1] < ROBOTS_TTL_SECONDS:
        return cached[0]
    parser = load_robots_policy(site, last_request, minimum_delay)
    cache[site] = (parser, now)
    return parser


def fetch_page(url, policy, last_request, minimum_delay):
    if not public_crawler.robots_can_fetch(policy, USER_AGENT, url):
        return {"blocked": True}
    delay = public_crawler.effective_host_delay(policy)
    opener = urllib.request.build_opener(
        SameOriginRobotsRedirectHandler(
            lambda target: public_crawler.robots_can_fetch(policy, USER_AGENT, target)
        )
    )
    _pace(url, delay, last_request)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        final_url = clean_url(response.geturl())
        if not final_url or not same_origin(url, final_url):
            raise CrossOriginRedirectError("final response changed origin")
        if not public_crawler.robots_can_fetch(policy, USER_AGENT, final_url):
            return {"blocked": True, "final_url": final_url}
        content_type = response.headers.get_content_type()
        x_robots = response.headers.get_all("X-Robots-Tag") or []
        status = response.status
        body = response.read(PAGE_MAX_BYTES + 1)
    if len(body) > PAGE_MAX_BYTES:
        return {"skipped": True, "reason": "page exceeded safe response size"}
    if content_type != "text/html":
        return {"skipped": True, "reason": "non-HTML response"}
    parser = MediaPageParser()
    parser.feed(body.decode("utf-8", "replace"))
    directives = parser.directives | public_crawler.parse_indexing_directives(x_robots)
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:2000]
    description = re.sub(r"\s+", " ", parser.description).strip()[:5000]
    text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()[:200000]
    public_crawler.apply_access_challenge_policy(title, description, text, directives)
    if "noindex" in directives:
        return {"noindex": True, "final_url": final_url}
    text, description = public_crawler.apply_snippet_policy(text, description, directives)
    links = [
        raw for raw in parser.links
        if clean_url(urllib.parse.urljoin(final_url, raw))
    ]
    media = []
    for raw, media_type, alt_text in parser.media:
        candidate = clean_url(urllib.parse.urljoin(final_url, raw))
        if candidate:
            media.append((candidate, media_type, alt_text))
    return {
        "url": final_url,
        "host": public_crawler.host_key(final_url),
        "title": title or public_crawler.fallback_page_title(final_url),
        "description": description,
        "text": text,
        "links": links,
        "media": media,
        "nofollow": "nofollow" in directives,
        "status": status,
        "content_type": content_type,
    }


def _mark_existing_disallowed(conn, url):
    with conn.cursor() as cur:
        cur.execute(
            "update eh_quad.eh_documents set robots_allowed=false, updated_at=now() "
            "where canonical_url=%s and robots_allowed is distinct from false",
            (url,),
        )
    conn.commit()


def _queue_finish(conn, queue_id, status, error=None, retry_seconds=0):
    with conn.cursor() as cur:
        cur.execute(
            """
            update eh_quad.eh_crawl_queue
            set status=%s, locked_at=null, last_error=%s,
                next_attempt_at=case when %s > 0 then now() + (%s * interval '1 second') else now() end
            where id=%s
            """,
            (status, error[:1000] if error else None, retry_seconds, retry_seconds, queue_id),
        )
    conn.commit()


def _set_host_lease(conn, host, lane, worker_id, seconds):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into eh_quad.eh_host_leases(host,lane,lease_owner,leased_until,updated_at)
            values (%s,%s,%s,now() + (%s * interval '1 second'),now())
            on conflict (host) do update set
              lane=excluded.lane, lease_owner=excluded.lease_owner,
              leased_until=excluded.leased_until, updated_at=now()
            """,
            (host, lane, worker_id, max(0.0, float(seconds))),
        )
    conn.commit()


def _claim_one(conn, worker_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            with candidate as (
              select q.id
              from eh_quad.eh_crawl_queue q
              join eh_quad.eh_lane_state lane
                on lane.lane=q.lane and lane.enabled is true
              where q.status='pending'
                and q.next_attempt_at <= now()
                and not exists (
                  select 1 from eh_quad.eh_host_leases h
                  where h.host=q.host and h.leased_until > now()
                )
              order by q.priority asc, q.depth asc, q.id asc
              for update of q skip locked
              limit 1
            )
            update eh_quad.eh_crawl_queue q
            set status='in_progress', locked_at=now(), attempts=q.attempts+1
            from candidate
            where q.id=candidate.id
            returning q.id,q.url,q.host,q.lane,q.depth,q.attempts
            """
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """
                insert into eh_quad.eh_host_leases(host,lane,lease_owner,leased_until,updated_at)
                values (%s,%s,%s,now() + interval '1 hour',now())
                on conflict (host) do update set
                  lane=excluded.lane, lease_owner=excluded.lease_owner,
                  leased_until=excluded.leased_until, updated_at=now()
                """,
                (row[2], row[3], worker_id),
            )
    conn.commit()
    return row


def _create_run(conn, lane, worker_id):
    with conn.cursor() as cur:
        cur.execute(
            "insert into eh_quad.eh_crawl_runs(lane,worker_id) values (%s,%s) returning id",
            (lane, worker_id),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _run_increment(conn, run_id, **increments):
    allowed = {"claimed_count", "fetched_count", "indexed_count", "failed_count"}
    if not increments or any(key not in allowed for key in increments):
        return
    assignments = ", ".join(f"{key}={key}+%s" for key in increments)
    with conn.cursor() as cur:
        cur.execute(
            f"update eh_quad.eh_crawl_runs set {assignments} where id=%s",
            (*increments.values(), run_id),
        )
    conn.commit()


def _finish_run(conn, run_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            update eh_quad.eh_crawl_runs
            set finished_at=now(),
                status=case when failed_count=0 then 'succeeded' else 'partial' end
            where id=%s and status='running'
            """,
            (run_id,),
        )
    conn.commit()


def _record_page(conn, item, page, max_depth):
    content = page["title"] + "\n" + page["description"] + "\n" + page["text"]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    lane = item[3] if item[3] in VALID_LANES else classify_lane(page["url"])
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into eh_quad.eh_documents as target
              (canonical_url,source_host,lane,title,description,body_text,http_status,
               content_type,content_hash,fetched_at,robots_allowed)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),true)
            on conflict (canonical_url) do update set
              source_host=excluded.source_host, lane=excluded.lane,
              title=excluded.title, description=excluded.description,
              body_text=excluded.body_text, http_status=excluded.http_status,
              content_type=excluded.content_type, content_hash=excluded.content_hash,
              fetched_at=now(), robots_allowed=true, updated_at=now()
            where target.content_hash is distinct from excluded.content_hash
               or target.lane is distinct from excluded.lane
               or target.robots_allowed is distinct from true
            returning id
            """,
            (page["url"], page["host"], lane, page["title"], page["description"],
             page["text"], page["status"], page["content_type"], digest),
        )
        row = cur.fetchone()
        if row:
            document_id = row[0]
        else:
            cur.execute("select id from eh_quad.eh_documents where canonical_url=%s", (page["url"],))
            document_id = cur.fetchone()[0]
        for media_url, media_type, alt_text in page["media"]:
            cur.execute(
                """
                insert into eh_quad.eh_media(document_id,page_url,media_url,media_type,alt_text)
                values (%s,%s,%s,%s,%s)
                on conflict (page_url,media_url) do update set
                  document_id=excluded.document_id, media_type=excluded.media_type,
                  alt_text=excluded.alt_text
                """,
                (document_id, page["url"], media_url, media_type, alt_text),
            )
        if item[4] < max_depth and not page["nofollow"]:
            for raw_link in page["links"]:
                candidate = clean_url(urllib.parse.urljoin(page["url"], raw_link))
                if not candidate or public_crawler.host_key(candidate) != page["host"]:
                    continue
                cur.execute(
                    """
                    insert into eh_quad.eh_crawl_queue(url,host,lane,depth,priority)
                    values (%s,%s,%s,%s,100)
                    on conflict (url,lane) do nothing
                    """,
                    (candidate, public_crawler.host_key(candidate), classify_lane(candidate), item[4] + 1),
                )
    conn.commit()


def _recover_stale_claims(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            update eh_quad.eh_crawl_queue
            set status='pending', locked_at=null, next_attempt_at=now(),
                last_error='Recovered stale in-progress claim'
            where status='in_progress'
              and locked_at < now() - (%s * interval '1 second')
            """,
            (STALE_CLAIM_SECONDS,),
        )
        recovered = cur.rowcount
    conn.commit()
    return recovered


def _seed_queue(conn):
    seed_path = ROOT / "crawler" / "seeds.txt"
    if not seed_path.exists():
        return 0
    inserted = 0
    with seed_path.open(encoding="utf-8") as source, conn.cursor() as cur:
        for line in source:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            url = clean_url(raw)
            if not url:
                continue
            host = public_crawler.host_key(url)
            cur.execute(
                """
                insert into eh_quad.eh_crawl_queue(url,host,lane,depth,priority)
                values (%s,%s,%s,0,10)
                on conflict (url,lane) do nothing
                """,
                (url, host, classify_lane(url)),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def main():
    validate_schema()
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is required; no database was modified.")
    test_mode = os.environ.get("EH_CRAWL_TEST_MODE") == "1"
    if test_mode and os.environ.get("EH_CRAWL_ALLOW_LOOPBACK_TEST") != "1":
        raise SystemExit("Loopback test mode requires EH_CRAWL_ALLOW_LOOPBACK_TEST=1.")
    if test_mode:
        public_crawler.HOST_MIN_INTERVAL = 0.0
    max_pages = bounded_int("EH_CRAWL_MAX_PAGES", 80, 1, MAX_PAGES_HARD_CAP)
    max_items = bounded_int("EH_CRAWL_MAX_ITEMS", 120, 1, MAX_ITEMS_HARD_CAP)
    max_depth = bounded_int("EH_CRAWL_MAX_DEPTH", 3, 0, MAX_DEPTH_HARD_CAP)
    minimum_delay = 0.0 if test_mode else max(1.0, float(os.environ.get("EH_CRAWL_HOST_DELAY", "1")))
    import psycopg

    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    robots_cache = {}
    last_request = {}
    run_ids = {}
    indexed = 0
    processed = 0
    failures = 0
    run_started = time.monotonic()

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        if test_mode:
            with conn.cursor() as cur:
                cur.execute("select current_database()")
                db_name = cur.fetchone()[0]
            if "test" not in db_name.lower():
                raise SystemExit("EH_CRAWL_TEST_MODE is restricted to a database whose name contains 'test'.")
        with conn.cursor() as cur:
            cur.execute("select pg_try_advisory_lock(%s)", (LOCK_KEY,))
            locked = cur.fetchone()[0]
        conn.commit()
        if not locked:
            print("Another eh_quad database crawler worker holds the singleton lock; exiting safely.")
            return 0
        try:
            recovered = _recover_stale_claims(conn)
            seeded = _seed_queue(conn) if os.environ.get("EH_CRAWL_SEED", "1") == "1" else 0
            print(f"Recovered stale claims: {recovered}; new seed URLs: {seeded}")
            while (
                processed < max_items
                and indexed < max_pages
                and time.monotonic() - run_started < MAX_RUN_SECONDS
            ):
                item = _claim_one(conn, worker_id)
                if not item:
                    break
                queue_id, url, host, lane, depth, attempts = item
                processed += 1
                if lane not in run_ids:
                    run_ids[lane] = _create_run(conn, lane, worker_id)
                run_id = run_ids[lane]
                _run_increment(conn, run_id, claimed_count=1)
                policy = None
                try:
                    if not is_safe_target(url, test_mode=test_mode):
                        _queue_finish(conn, queue_id, "skipped", "Non-public or unresolved destination rejected")
                        _set_host_lease(conn, host, lane, worker_id, minimum_delay)
                        continue
                    parsed = urllib.parse.urlsplit(url)
                    site = f"{parsed.scheme}://{parsed.netloc}"
                    policy = get_robots_policy(site, robots_cache, last_request, minimum_delay)
                    if not public_crawler.robots_can_fetch(policy, USER_AGENT, url):
                        _mark_existing_disallowed(conn, url)
                        _queue_finish(conn, queue_id, "skipped", "Disallowed by robots.txt")
                        _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy))
                        continue
                    page = fetch_page(url, policy, last_request, minimum_delay)
                    if page.get("blocked"):
                        _mark_existing_disallowed(conn, url)
                        _mark_existing_disallowed(conn, page.get("final_url", url))
                        _queue_finish(conn, queue_id, "skipped", "Disallowed by robots.txt after redirect")
                        _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy))
                        continue
                    if page.get("noindex"):
                        _mark_existing_disallowed(conn, url)
                        _mark_existing_disallowed(conn, page.get("final_url", url))
                        _queue_finish(conn, queue_id, "skipped", "Page explicitly marked noindex")
                        _run_increment(conn, run_id, fetched_count=1)
                        _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy))
                        continue
                    if page.get("skipped"):
                        _queue_finish(conn, queue_id, "skipped", page.get("reason", "response skipped"))
                        _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy))
                        continue
                    _run_increment(conn, run_id, fetched_count=1)
                    _record_page(conn, item, page, max_depth)
                    _queue_finish(conn, queue_id, "done")
                    _run_increment(conn, run_id, indexed_count=1)
                    indexed += 1
                    _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy))
                except RobotsUnavailable as error:
                    conn.rollback()
                    retry = error.retry_after
                    status = "pending" if attempts < 5 else "failed"
                    _queue_finish(conn, queue_id, status, str(error), retry)
                    _set_host_lease(conn, host, lane, worker_id, retry)
                    _run_increment(conn, run_id, failed_count=1)
                    failures += 1
                except urllib.error.HTTPError as error:
                    conn.rollback()
                    retry = retry_after_seconds(
                        error,
                        DEFAULT_429_BACKOFF_SECONDS if error.code == 429 else DEFAULT_5XX_BACKOFF_SECONDS,
                    )
                    if error.code in (408, 429, 500, 502, 503, 504):
                        status = "pending" if attempts < 5 else "failed"
                        _queue_finish(conn, queue_id, status, f"HTTP {error.code}", retry)
                        _set_host_lease(conn, host, lane, worker_id, retry)
                    else:
                        _queue_finish(conn, queue_id, "skipped", f"HTTP {error.code}")
                        _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy))
                    _run_increment(conn, run_id, failed_count=1)
                    failures += 1
                except RobotsDeniedRedirectError as error:
                    conn.rollback()
                    _mark_existing_disallowed(conn, url)
                    _queue_finish(conn, queue_id, "skipped", str(error))
                    _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy) if policy is not None else minimum_delay)
                    continue
                except CrossOriginRedirectError as error:
                    conn.rollback()
                    _queue_finish(conn, queue_id, "skipped", str(error))
                    _set_host_lease(conn, host, lane, worker_id, public_crawler.effective_host_delay(policy) if policy is not None else minimum_delay)
                except Exception as error:
                    conn.rollback()
                    retry = DEFAULT_NETWORK_BACKOFF_SECONDS
                    status = "pending" if attempts < 5 else "failed"
                    _queue_finish(conn, queue_id, status, f"{type(error).__name__}: {error}", retry)
                    _set_host_lease(conn, host, lane, worker_id, retry)
                    _run_increment(conn, run_id, failed_count=1)
                    failures += 1
            for run_id in run_ids.values():
                _finish_run(conn, run_id)
        finally:
            with conn.cursor() as cur:
                cur.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))
            conn.commit()

    print(f"eh_quad crawler finished: processed={processed}, indexed={indexed}, failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
