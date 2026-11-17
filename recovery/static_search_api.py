#!/usr/bin/env python3
"""Database-independent Everything Hub search API over the committed public index.

Reads public-search/shard-*.json at startup. No Supabase, database, API key,
third-party search provider, or external runtime dependency is required.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = os.environ.get("EH_INDEX_DIR", "/app/public-search")
HOST = os.environ.get("EH_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
MAX_QUERY = 160
MAX_LIMIT = 20
VALID_LANES = {"general", "science", "news", "technology", "business",
               "government", "culture", "nature"}
TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.I)


def load_index():
    pages = []
    for filename in sorted(glob.glob(os.path.join(ROOT, "shard-*.json"))):
        with open(filename, "r", encoding="utf-8") as handle:
            shard = json.load(handle)
        for page in shard.get("pages", []):
            url = page.get("url", "")
            if not url.startswith(("https://", "http://")):
                continue
            pages.append({
                "url": url,
                "title": str(page.get("title") or ""),
                "description": str(page.get("description") or ""),
                "text": str(page.get("text") or "")[:12000],
                "lane": str(page.get("lane") or "general"),
            })
    return pages


PAGES = load_index()
DOCS = []
for page in PAGES:
    title_tokens = Counter(TOKEN_RE.findall(page["title"].lower()))
    desc_tokens = Counter(TOKEN_RE.findall(page["description"].lower()))
    text_tokens = Counter(TOKEN_RE.findall(page["text"].lower()))
    url_tokens = Counter(TOKEN_RE.findall(page["url"].lower()))
    DOCS.append((page, title_tokens, desc_tokens, text_tokens, url_tokens))


def search(query, lane, limit, offset):
    terms = list(dict.fromkeys(TOKEN_RE.findall(query.lower())))
    if not terms:
        return {"query": query, "total": 0, "results": [], "hasMore": False}
    ranked = []
    for page, title, desc, body, url in DOCS:
        if lane and page["lane"] != lane:
            continue
        score = 0
        matched = 0
        for term in terms:
            points = (title[term] * 12 + desc[term] * 6 +
                      url[term] * 4 + min(body[term], 8))
            if points:
                matched += 1
                score += points
        if matched:
            # Prefer documents matching more of the query, then title hits.
            score += matched * 5
            if matched == len(terms):
                score += 15
            ranked.append((score, page))
    ranked.sort(key=lambda item: (-item[0], item[1]["url"]))
    total = len(ranked)
    selected = ranked[offset:offset + limit]
    return {
        "query": query, "total": total, "limit": limit, "offset": offset,
        "tentacle": lane or None,
        "hasMore": offset + len(selected) < total,
        "results": [
            {"url": p["url"], "title": p["title"] or p["url"],
             "description": p["description"], "tentacle": p["lane"],
             "score": score}
            for score, p in selected
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "EverythingHubStaticSearch/1.0"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", os.environ.get("EH_CORS_ORIGIN", "*"))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path == "/healthz":
            self.send_json(200, {"status": "ok", "service": "static-search",
                                 "database": "not_required", "indexed_pages": len(PAGES)})
            return
        if route.path != "/search":
            self.send_json(404, {"error": "not_found"})
            return
        params = parse_qs(route.query)
        query = (params.get("q") or [""])[0].strip()
        if not query or len(query) > MAX_QUERY:
            self.send_json(400, {"error": "q_required", "max_query_length": MAX_QUERY})
            return
        try:
            limit = int((params.get("limit") or ["10"])[0])
            offset = int((params.get("offset") or ["0"])[0])
        except ValueError:
            self.send_json(400, {"error": "invalid_pagination"})
            return
        if not 1 <= limit <= MAX_LIMIT or not 0 <= offset <= 5000:
            self.send_json(400, {"error": "pagination_out_of_range"})
            return
        lane = (params.get("tentacle") or [""])[0].strip().lower()
        if lane not in VALID_LANES:
            lane = ""
        self.send_json(200, search(query, lane, limit, offset))

    def log_message(self, fmt, *args):
        print("static-search:", args[0] if args else "request")


if __name__ == "__main__":
    print(f"Everything Hub static search ready: {len(PAGES)} pages")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
