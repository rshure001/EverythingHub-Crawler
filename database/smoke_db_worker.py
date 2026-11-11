#!/usr/bin/env python3
"""End-to-end smoke test for the database-native crawler worker.

Run only against the isolated PostgreSQL schema created by import-smoke-test.yml.
"""
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = os.environ.get("EH_DB_SCHEMA", "eh_import_test")
DATABASE_URL = os.environ["DATABASE_URL"]

class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain; charset=utf-8"
        elif self.path == "/fixture":
            body = (b'<html><head><title>Everything Hub worker fixture</title>'
                    b'<meta name="description" content="Direct database crawl test"></head>'
                    b'<body><main>Searchable copper compass fixture text.'
                    b'<a href="/child">next page</a></main></body></html>')
            content_type = "text/html; charset=utf-8"
        else:
            body = b"<html><title>Child fixture</title><body>Child page</body></html>"
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/fixture"
    try:
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute(
                f"""insert into "{SCHEMA}".eh_crawl_queue
                    (url,host,lane,depth,priority)
                    values (%s,%s,'general',0,0)
                    on conflict (url,lane) do update
                    set status='pending', attempts=0, next_attempt_at=now(),
                        priority=0, locked_at=null""",
                (url, "127.0.0.1"),
            )
        env = os.environ.copy()
        env["EH_CRAWL_MAX_PAGES"] = "1"
        env["EH_CRAWL_HOST_DELAY"] = "0"
        subprocess.run(
            [sys.executable, str(ROOT / "database" / "crawl_worker.py")],
            cwd=ROOT, env=env, check=True, timeout=45,
        )
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute(
                f"""select title, description, body_text from "{SCHEMA}".eh_documents
                    where canonical_url=%s""",
                (url,),
            )
            row = cur.fetchone()
            assert row is not None, "worker did not write the fetched page to eh_documents"
            assert row[0] == "Everything Hub worker fixture", row
            assert "copper compass" in row[2], row
            cur.execute(
                f"""select status from "{SCHEMA}".eh_crawl_queue
                    where url=%s and lane='general'""",
                (url,),
            )
            assert cur.fetchone()[0] == "done", "worker did not complete the claimed queue item"
            cur.execute(
                f"""select count(*) from "{SCHEMA}".eh_crawl_queue
                    where url=%s and status='pending'""",
                (f"http://127.0.0.1:{server.server_port}/child",),
            )
            assert cur.fetchone()[0] == 1, "worker did not enqueue the discovered same-host link"
        print("Database-native crawler smoke test passed: fetched, indexed, completed, and enqueued.")
    finally:
        server.shutdown()
        server.server_close()

if __name__ == "__main__":
    main()
