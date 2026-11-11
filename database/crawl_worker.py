#!/usr/bin/env python3
"""Database-native Everything Hub crawler worker.

Consumes eh_crawl_queue and writes fetched pages directly to eh_documents.
This is deliberately a single-worker first cut: it honors robots.txt, spaces
requests to each host, retries transient failures, and never touches Dog Tag Day.
"""
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

import psycopg

SCHEMA = os.environ.get("EH_DB_SCHEMA", "public")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USER_AGENT = "EverythingHubCrawler/2.0 (+https://github.com/rshure001/EverythingHub-Crawler)"
MAX_PAGES = int(os.environ.get("EH_CRAWL_MAX_PAGES", "80"))
MAX_DEPTH = int(os.environ.get("EH_CRAWL_MAX_DEPTH", "3"))
HOST_DELAY = float(os.environ.get("EH_CRAWL_HOST_DELAY", "1.5"))
REQUEST_TIMEOUT = 15
LANES = {"general", "science", "news", "technology", "business", "government", "culture", "nature"}

if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SCHEMA):
    raise SystemExit("EH_DB_SCHEMA must be a simple PostgreSQL schema name.")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required; no database was modified.")

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts = []
        self.description = ""
        self.text_parts = []
        self.links = []
        self.in_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content", "")
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if not self.skip_depth and data.strip():
            self.text_parts.append(data.strip())

def clean_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")
             and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                    parsed.path or "/", urllib.parse.urlencode(query), ""))

def lane_for_url(url):
    host = urllib.parse.urlsplit(url).hostname.lower()
    rules = (
        ("government", (".gov", ".mil")),
        ("science", ("science", "nature.com", "arxiv", "pubmed", "nih.gov")),
        ("news", ("news", "reuters", "apnews", "bbc", "npr.org")),
        ("technology", ("tech", "github", "computer", "wired")),
        ("business", ("business", "finance", "market", "econom", "invest")),
        ("culture", ("museum", "arts", "culture", "history", "library")),
        ("nature", ("weather", "climate", "wildlife", "earth", "noaa.gov")),
    )
    for lane, needles in rules:
        if any(n in host for n in needles):
            return lane
    return "general"

def fetch_page(url, robots_cache):
    parsed_url = urllib.parse.urlsplit(url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    if origin not in robots_cache:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(origin + "/robots.txt")
        try:
            parser.read()
        except Exception:
            parser.disallow_all = True
        robots_cache[origin] = parser
    if not robots_cache[origin].can_fetch(USER_AGENT, url):
        return {"blocked": True}

    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        final_url = clean_url(response.geturl())
        content_type = response.headers.get_content_type()
        body = response.read(2_000_000)
    if not final_url or content_type != "text/html":
        return {"skipped": True}
    parser = PageParser()
    parser.feed(body.decode("utf-8", "replace"))
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:2000]
    description = re.sub(r"\s+", " ", parser.description).strip()[:5000]
    text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()[:200000]
    links = []
    final_host = urllib.parse.urlsplit(final_url).hostname
    for href in parser.links:
        candidate = clean_url(urllib.parse.urljoin(final_url, href))
        if candidate and urllib.parse.urlsplit(candidate).hostname == final_host:
            links.append(candidate)
    return {
        "url": final_url, "host": final_host, "title": title,
        "description": description, "text": text, "links": links,
        "content_type": content_type,
    }

def claim_one(conn):
    sql = f"""
      with candidate as (
        select id from "{SCHEMA}".eh_crawl_queue
        where status = 'pending' and next_attempt_at <= now()
        order by priority asc, depth asc, id asc
        for update skip locked limit 1
      )
      update "{SCHEMA}".eh_crawl_queue q
      set status = 'in_progress', locked_at = now(), attempts = attempts + 1
      from candidate where q.id = candidate.id
      returning q.id, q.url, q.lane, q.depth, q.attempts
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    conn.commit()
    return row

def finish_queue_item(conn, queue_id, status, error=None):
    with conn.cursor() as cur:
        cur.execute(
            f"""update "{SCHEMA}".eh_crawl_queue
                set status = %s, locked_at = null, last_error = %s
                where id = %s""",
            (status, error[:1000] if error else None, queue_id),
        )
    conn.commit()

def record_page(conn, item, page):
    lane = item[2] if item[2] in LANES else lane_for_url(page["url"])
    with conn.cursor() as cur:
        cur.execute(
            f"""insert into "{SCHEMA}".eh_documents
                (canonical_url, source_host, lane, title, description, body_text,
                 content_type, content_hash, fetched_at, robots_allowed)
                values (%s,%s,%s,%s,%s,%s,%s,md5(%s),now(),true)
                on conflict (canonical_url) do update set
                  source_host=excluded.source_host, lane=excluded.lane,
                  title=excluded.title, description=excluded.description,
                  body_text=excluded.body_text, content_type=excluded.content_type,
                  content_hash=excluded.content_hash, fetched_at=now(), updated_at=now()
                where "{SCHEMA}".eh_documents.content_hash is distinct from excluded.content_hash""",
            (page["url"], page["host"], lane, page["title"], page["description"],
             page["text"], page["content_type"], page["title"] + "\n" + page["description"] + "\n" + page["text"]),
        )
        if item[3] < MAX_DEPTH:
            for link in page["links"]:
                cur.execute(
                    f"""insert into "{SCHEMA}".eh_crawl_queue
                        (url, host, lane, depth, priority)
                        values (%s,%s,%s,%s,100)
                        on conflict (url,lane) do nothing""",
                    (link, urllib.parse.urlsplit(link).hostname.lower(),
                     lane_for_url(link), item[3] + 1),
                )
    conn.commit()

def seed_queue(conn):
    seed_path = os.path.join(os.path.dirname(__file__), "..", "crawler", "seeds.txt")
    count = 0
    with open(seed_path, encoding="utf-8") as seed_file, conn.cursor() as cur:
        for line in seed_file:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            url = clean_url(raw)
            if not url:
                continue
            host = urllib.parse.urlsplit(url).hostname.lower()
            cur.execute(
                f"""insert into "{SCHEMA}".eh_crawl_queue (url,host,lane,depth,priority)
                    values (%s,%s,%s,0,10) on conflict (url,lane) do nothing""",
                (url, host, lane_for_url(url)),
            )
            count += cur.rowcount
    conn.commit()
    return count

def main():
    robots_cache = {}
    last_host_request = {}
    completed = 0
    with psycopg.connect(DATABASE_URL, connect_timeout=10) as conn:
        seeded = seed_queue(conn)
        print(f"Seeded {seeded} new starting URLs into the database queue.")
        while completed < MAX_PAGES:
            item = claim_one(conn)
            if not item:
                break
            queue_id, url, lane, depth, attempts = item
            host = urllib.parse.urlsplit(url).hostname.lower()
            wait = HOST_DELAY - (time.monotonic() - last_host_request.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            try:
                page = fetch_page(url, robots_cache)
                last_host_request[host] = time.monotonic()
                if page.get("blocked"):
                    finish_queue_item(conn, queue_id, "skipped", "Disallowed by robots.txt or robots.txt unavailable")
                elif page.get("skipped"):
                    finish_queue_item(conn, queue_id, "skipped", "Non-HTML response or invalid final URL")
                else:
                    record_page(conn, item, page)
                    finish_queue_item(conn, queue_id, "done")
                    completed += 1
            except Exception as exc:
                retry = attempts < 4
                with conn.cursor() as cur:
                    cur.execute(
                        f"""update "{SCHEMA}".eh_crawl_queue
                            set status=%s, locked_at=null, last_error=%s,
                                next_attempt_at=now() + (%s * interval '1 minute')
                            where id=%s""",
                        ("pending" if retry else "failed", str(exc)[:1000],
                         min(60, 2 ** attempts), queue_id),
                    )
                conn.commit()
                print(f"Fetch failed for {url}: {exc}")
        with conn.cursor() as cur:
            cur.execute(f"""select lane, status, count(*) from "{SCHEMA}".eh_crawl_queue
                            group by lane,status order by lane,status""")
            queue_counts = cur.fetchall()
            cur.execute(f"""select lane, count(*) from "{SCHEMA}".eh_documents
                            group by lane order by lane""")
            document_counts = cur.fetchall()
        print(f"Database crawler finished: {completed} HTML pages indexed this run.")
        print("Queue by lane/status:", queue_counts)
        print("Documents by lane:", document_counts)

if __name__ == "__main__":
    main()
