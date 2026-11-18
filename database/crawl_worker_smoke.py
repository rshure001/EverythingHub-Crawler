#!/usr/bin/env python3
"""Integration smoke test for the eh_quad crawler using only a local HTTP fixture."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("EH_DB_SCHEMA", "eh_quad")
if SCHEMA != "eh_quad":
    raise SystemExit("The crawler smoke test is locked to eh_quad.")


class FixtureHandler(BaseHTTPRequestHandler):
    blocked_hits = 0
    child_hits = 0
    media_hits = 0
    target_hits = 0
    stale_hits = 0

    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\nDisallow: /blocked\nCrawl-delay: 0\n"
            content_type = "text/plain; charset=utf-8"
        elif self.path == "/fixture":
            body = (
                b'<html><head><title>Everything Hub worker fixture</title>'
                b'<meta name="description" content="Direct database crawl test"></head>'
                b'<body><main>Searchable copper compass fixture text.</main>'
                b'<img src="/image.png" alt="fixture image">'
                b'<video src="/clip.mp4"></video>'
                b'<a href="/child">same-host child</a></body></html>'
            )
            content_type = "text/html; charset=utf-8"
        elif self.path == "/blocked":
            type(self).blocked_hits += 1
            body = b"<html><title>Must not be fetched</title></html>"
            content_type = "text/html; charset=utf-8"
        elif self.path == "/noindex":
            body = (
                b'<html><head><title>Noindex fixture</title>'
                b'<meta name="robots" content="noindex"></head>'
                b'<body>Must not remain searchable.</body></html>'
            )
            content_type = "text/html; charset=utf-8"
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/target")
            self.end_headers()
            return
        elif self.path == "/target":
            type(self).target_hits += 1
            body = b"cross-host redirect target must not be fetched"
            content_type = "text/html; charset=utf-8"
        elif self.path == "/child":
            type(self).child_hits += 1
            body = b"<html><title>Child fixture</title><body>Child page</body></html>"
            content_type = "text/html; charset=utf-8"
        elif self.path == "/stale":
            type(self).stale_hits += 1
            body = b"<html><title>Recovered stale claim</title><body>Recovered lease fixture</body></html>"
            content_type = "text/html; charset=utf-8"
        elif self.path in ("/image.png", "/clip.mp4"):
            type(self).media_hits += 1
            body = b"not fetched by discovery-only crawler"
            content_type = "application/octet-stream"
        else:
            body = b"not found"
            content_type = "text/plain; charset=utf-8"
            self.send_response(404)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    host = f"127.0.0.1:{port}"
    urls = {
        "fixture": f"http://{host}/fixture",
        "blocked": f"http://{host}/blocked",
        "noindex": f"http://{host}/noindex",
        "child": f"http://{host}/child",
        "redirect": f"http://{host}/redirect",
        "stale": f"http://{host}/stale",
    }

    try:
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            for key in ("blocked", "noindex"):
                cur.execute(
                    """
                    insert into eh_quad.eh_documents
                      (canonical_url,source_host,lane,title,robots_allowed,content_hash)
                    values (%s,%s,'general','pre-existing fixture',true,'pre-existing')
                    on conflict (canonical_url) do update
                    set robots_allowed=true, content_hash='pre-existing'
                    """,
                    (urls[key], host),
                )

            for priority, key in enumerate(("fixture", "blocked", "noindex", "redirect")):
                cur.execute(
                    """
                    insert into eh_quad.eh_crawl_queue
                      (url,host,lane,depth,priority,status,attempts,next_attempt_at,locked_at)
                    values (%s,%s,'general',0,%s,'pending',0,now(),null)
                    on conflict (url,lane) do update set
                      status='pending', attempts=0, next_attempt_at=now(),
                      priority=excluded.priority, locked_at=null, last_error=null
                    """,
                    (urls[key], host, priority),
                )
            cur.execute(
                """
                insert into eh_quad.eh_crawl_queue
                  (url,host,lane,depth,priority,status,attempts,next_attempt_at,locked_at)
                values (%s,%s,'general',0,4,'in_progress',1,now(),now() - interval '16 minutes')
                on conflict (url,lane) do update set
                  status='in_progress', attempts=1, next_attempt_at=now(),
                  locked_at=now() - interval '16 minutes', last_error='simulated abandoned worker'
                """,
                (urls["stale"], host),
            )
            cur.execute(
                """
                insert into eh_quad.eh_host_leases(host,lane,lease_owner,leased_until)
                values (%s,'general','simulated-crashed-worker',now() + interval '1 hour')
                on conflict (host) do update set
                  lease_owner=excluded.lease_owner, leased_until=excluded.leased_until,
                  updated_at=now()
                """,
                (host,),
            )

        env = os.environ.copy()
        env.update({
            "EH_DB_SCHEMA": "eh_quad",
            "EH_CRAWL_TEST_MODE": "1",
            "EH_CRAWL_ALLOW_LOOPBACK_TEST": "1",
            "EH_CRAWL_SEED": "0",
            "EH_CRAWL_MAX_PAGES": "4",
            "EH_CRAWL_MAX_ITEMS": "5",
            "EH_CRAWL_MAX_DEPTH": "1",
            "EH_CRAWL_HOST_DELAY": "0",
        })
        completed = subprocess.run(
            [sys.executable, str(ROOT / "database" / "crawl_worker.py")],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=90,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Crawler worker smoke process failed.\n"
                + completed.stdout[-6000:]
                + "\n"
                + completed.stderr[-6000:]
            )

        with psycopg.connect(DATABASE_URL) as conn:
            body = scalar(
                conn,
                "select body_text from eh_quad.eh_documents where canonical_url=%s",
                (urls["fixture"],),
            )
            assert body and "copper compass" in body, "fixture page was not indexed"
            assert scalar(
                conn,
                "select robots_allowed from eh_quad.eh_documents where canonical_url=%s",
                (urls["blocked"],),
            ) is False, "robots-denied existing row was not soft-disabled"
            assert scalar(
                conn,
                "select robots_allowed from eh_quad.eh_documents where canonical_url=%s",
                (urls["noindex"],),
            ) is False, "noindex existing row was not soft-disabled"
            assert scalar(
                conn,
                "select count(*) from eh_quad.eh_media where page_url=%s",
                (urls["fixture"],),
            ) == 2, "image/video references were not recorded"
            for key, expected in (
                ("fixture", "done"),
                ("blocked", "skipped"),
                ("noindex", "skipped"),
                ("redirect", "skipped"),
                ("stale", "done"),
            ):
                actual = scalar(
                    conn,
                    "select status from eh_quad.eh_crawl_queue where url=%s and lane='general'",
                    (urls[key],),
                )
                assert actual == expected, f"{key} queue status: {actual}"
            assert scalar(
                conn,
                "select status from eh_quad.eh_crawl_queue where url=%s and lane='general'",
                (urls["child"],),
            ) == "pending", "same-host child link was not queued"
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select claimed_count,fetched_count,indexed_count,failed_count,status
                    from eh_quad.eh_crawl_runs
                    where lane='general'
                    order by id desc limit 1
                    """
                )
                run_stats = cur.fetchone()
            assert run_stats == (5, 3, 2, 0, "succeeded"), f"unexpected run stats: {run_stats}"
            assert scalar(
                conn,
                "select count(*) from eh_quad.eh_host_leases where host=%s and lease_owner='simulated-crashed-worker'",
                (host,),
            ) == 0, "stale host lease was not released"

        assert FixtureHandler.blocked_hits == 0, "robots-disallowed URL was fetched"
        assert FixtureHandler.child_hits == 0, "queued child was fetched beyond the bounded run"
        assert FixtureHandler.media_hits == 0, "media assets were fetched instead of discovery-only"
        assert FixtureHandler.target_hits == 0, "cross-host redirect target was fetched"
        assert FixtureHandler.stale_hits == 1, "stale in-progress claim was not recovered and fetched"
        print(
            "eh_quad crawler integration smoke passed: indexing, robots denial, noindex, "
            "same-host queue, cross-host redirect rejection, media references, stale-claim host-lease "
            "recovery, run accounting, and schema lock."
        )
        print(completed.stdout.strip())
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
