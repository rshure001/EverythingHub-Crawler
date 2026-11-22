#!/usr/bin/env python3
"""Database-independent Everything Hub search over verified compressed shards."""
from __future__ import annotations

import hashlib
import json
import os
import re
import zlib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(os.environ.get("EH_INDEX_DIR", "/app/public-search"))
HOST = os.environ.get("EH_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
MAX_QUERY = 160
MAX_LIMIT = 20
MAX_PAGES = 250_000
MAX_SHARDS = 900
MAX_MANIFEST_BYTES = 256_000
MAX_SHARD_BYTES = 350_000
MAX_SHARD_UNCOMPRESSED_BYTES = 600_000
MAX_TOTAL_COMPRESSED_BYTES = 150_000_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 540_000_000
VALID_LANES = {"general", "science", "news", "technology", "business",
               "government", "culture", "nature"}
SHARD_NAME = re.compile(r"shard-\d{4}\.json(?:\.gz)?$")
SHA256 = re.compile(r"[a-f0-9]{64}$", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.I)


def bounded_gunzip(data: bytes, max_output: int) -> bytes:
    """Decompress one gzip member without ever accepting more than max_output."""
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    pending = data
    try:
        while pending:
            remaining = max_output - len(output) + 1
            if remaining <= 0:
                raise ValueError("shard exceeds decoded-byte safety limit")
            chunk = decoder.decompress(pending, remaining)
            output.extend(chunk)
            if len(output) > max_output:
                raise ValueError("shard exceeds decoded-byte safety limit")
            next_pending = decoder.unconsumed_tail
            if next_pending and not chunk:
                raise ValueError("gzip decompression stalled")
            pending = next_pending
        output.extend(decoder.flush())
    except zlib.error as exc:
        raise ValueError("shard gzip stream is invalid") from exc

    if len(output) > max_output:
        raise ValueError("shard exceeds decoded-byte safety limit")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("gzip stream is incomplete or has trailing data")
    return bytes(output)


def _manifest_int(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"manifest {name} is outside its safe bounds")
    return value


def _validate_page(page: dict, seen_urls: set[str]) -> dict:
    if not isinstance(page, dict):
        raise ValueError("catalog page must be an object")
    raw_url = page.get("url")
    if not isinstance(raw_url, str):
        raise ValueError("catalog page URL is missing")
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("catalog page URL is unsafe")
    if page.get("robots_allowed") is False or page.get("robotsAllowed") is False:
        raise ValueError("catalog contains a robots-denied page")
    if raw_url in seen_urls:
        raise ValueError("catalog contains a duplicate URL across shards")
    seen_urls.add(raw_url)

    lane = str(page.get("lane") or "general").lower()
    if lane not in VALID_LANES:
        lane = "general"
    return {
        "url": raw_url,
        "title": str(page.get("title") or "")[:500],
        "description": str(page.get("description") or "")[:500],
        "text": str(page.get("text") or "")[:12_000],
        "lane": lane,
    }


def load_index(root: Path | str = ROOT) -> list[dict]:
    """Load and verify every manifest-listed shard within explicit byte/page bounds."""
    root = Path(root)
    manifest_path = root / "manifest.json"
    manifest_raw = manifest_path.read_bytes()
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise ValueError("public catalog manifest exceeds its byte limit")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("public catalog manifest is invalid JSON") from exc

    if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("integrity") != "sha256":
        raise ValueError("public catalog manifest schema is unsupported")
    _manifest_int(manifest.get("updated_at"), "updated_at", 1, 9_007_199_254_740_991)
    total_pages_expected = _manifest_int(manifest.get("total_pages"), "total_pages", 1, MAX_PAGES)
    shard_count = _manifest_int(manifest.get("shard_count"), "shard_count", 1, MAX_SHARDS)
    shards = manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != shard_count:
        raise ValueError("public catalog shard count does not match its manifest")

    pages: list[dict] = []
    seen_files: set[str] = set()
    seen_urls: set[str] = set()
    total_compressed = 0
    total_uncompressed = 0

    for meta in shards:
        if not isinstance(meta, dict):
            raise ValueError("shard metadata must be an object")
        filename = meta.get("file")
        if not isinstance(filename, str) or not SHARD_NAME.fullmatch(filename):
            raise ValueError("manifest shard filename is unsafe")
        if filename in seen_files:
            raise ValueError("manifest contains a duplicate shard filename")
        seen_files.add(filename)

        compressed_bytes = _manifest_int(meta.get("bytes"), "shard bytes", 1, MAX_SHARD_BYTES)
        page_count = _manifest_int(meta.get("pages"), "shard pages", 1, 1000)
        digest = meta.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("manifest shard SHA-256 is invalid")

        is_gzip = filename.endswith(".json.gz")
        if is_gzip:
            uncompressed_bytes = _manifest_int(
                meta.get("uncompressed_bytes"),
                "shard uncompressed bytes",
                1,
                MAX_SHARD_UNCOMPRESSED_BYTES,
            )
        else:
            uncompressed_bytes = _manifest_int(
                meta.get("uncompressed_bytes", compressed_bytes),
                "legacy shard uncompressed bytes",
                1,
                MAX_SHARD_UNCOMPRESSED_BYTES,
            )
            if uncompressed_bytes != compressed_bytes:
                raise ValueError("legacy shard byte counts do not match")

        total_compressed += compressed_bytes
        total_uncompressed += uncompressed_bytes
        if total_compressed > MAX_TOTAL_COMPRESSED_BYTES:
            raise ValueError("public catalog exceeds its aggregate compressed-byte limit")
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("public catalog exceeds its aggregate decoded-byte limit")

        shard_path = root / filename
        raw = shard_path.read_bytes()
        if len(raw) != compressed_bytes:
            raise ValueError(f"{filename} compressed byte count does not match its manifest")
        if hashlib.sha256(raw).hexdigest().lower() != digest.lower():
            raise ValueError(f"{filename} SHA-256 does not match its manifest")

        decoded = bounded_gunzip(raw, MAX_SHARD_UNCOMPRESSED_BYTES) if is_gzip else raw
        if len(decoded) != uncompressed_bytes:
            raise ValueError(f"{filename} decoded byte count does not match its manifest")
        try:
            payload = json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{filename} is not valid UTF-8 JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("pages"), list)
            or len(payload["pages"]) != page_count
        ):
            raise ValueError(f"{filename} page count or schema is invalid")

        for page in payload["pages"]:
            pages.append(_validate_page(page, seen_urls))

    if len(pages) != total_pages_expected:
        raise ValueError("verified shard page total does not match the public manifest")
    return pages


PAGES = [] if os.environ.get("EH_SKIP_INDEX_LOAD") == "1" else load_index()
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
