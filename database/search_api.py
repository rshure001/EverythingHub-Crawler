#!/usr/bin/env python3
"""Small authenticated PostgreSQL full-text search API for Everything Hub.

Run with DATABASE_URL, EH_SEARCH_API_TOKEN, and optionally EH_DB_SCHEMA.
GET /healthz is a database readiness check. GET /search?q=...&limit=10&offset=0
requires Authorization: Bearer <EH_SEARCH_API_TOKEN>.
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SCHEMA = os.environ.get("EH_DB_SCHEMA", "public")
TOKEN = os.environ.get("EH_SEARCH_API_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
HOST = os.environ.get("EH_SEARCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("EH_SEARCH_PORT", "8080"))

if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SCHEMA):
    raise SystemExit("EH_DB_SCHEMA must be a simple PostgreSQL schema name.")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required.")
if not TOKEN:
    raise SystemExit("EH_SEARCH_API_TOKEN is required; refusing unauthenticated search.")


def connect():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary]>=3.2,<4.") from exc
    return psycopg.connect(DATABASE_URL, connect_timeout=5)


class Handler(BaseHTTPRequestHandler):
    server_version = "EverythingHubSearch/0.1"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path == "/healthz":
            try:
                with connect() as conn, conn.cursor() as cur:
                    cur.execute("select 1")
                    cur.fetchone()
                self.send_json(200, {"status": "ok", "database": "connected"})
            except Exception:
                self.send_json(503, {"status": "unavailable", "database": "disconnected"})
            return

        if route.path != "/search":
            self.send_json(404, {"error": "not_found"})
            return
        if self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
            self.send_json(401, {"error": "unauthorized"})
            return

        params = parse_qs(route.query, keep_blank_values=True)
        query = (params.get("q") or [""])[0].strip()
        if not query or len(query) > 200:
            self.send_json(400, {"error": "q_required", "max_query_length": 200})
            return
        try:
            limit = int((params.get("limit") or ["10"])[0])
            offset = int((params.get("offset") or ["0"])[0])
        except ValueError:
            self.send_json(400, {"error": "invalid_pagination"})
            return
        if not 1 <= limit <= 50 or not 0 <= offset <= 100000:
            self.send_json(400, {"error": "pagination_out_of_range", "limit_max": 50})
            return

        sql = f"""
          select canonical_url, source_host, title, description,
                 ts_rank(search_vector, plainto_tsquery('english', %s)) as score
          from "{SCHEMA}".eh_documents
          where search_vector @@ plainto_tsquery('english', %s)
          order by score desc, fetched_at desc
          limit %s offset %s
        """
        count_sql = f"""
          select count(*) from "{SCHEMA}".eh_documents
          where search_vector @@ plainto_tsquery('english', %s)
        """
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(count_sql, (query,))
                total = cur.fetchone()[0]
                cur.execute(sql, (query, query, limit, offset))
                rows = cur.fetchall()
            results = [{
                "url": row[0], "host": row[1], "title": row[2],
                "description": row[3], "score": float(row[4])
            } for row in rows]
            self.send_json(200, {
                "query": query, "total": total, "limit": limit,
                "offset": offset, "results": results
            })
        except Exception:
            self.send_json(503, {"error": "search_unavailable"})

    def log_message(self, fmt, *args):
        # Keep request logs concise and avoid logging search terms or credentials.
        print("search-api:", args[0] if args else "request")


if __name__ == "__main__":
    print(f"Everything Hub search API listening on {HOST}:{PORT}; schema={SCHEMA}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
