#!/usr/bin/env python3
"""Read dataset landing-page URLs from the normalized catalog staging manifest."""
from __future__ import annotations
import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit

def load_catalog_seed_urls(path: Path, limit: int = 20000) -> list[str]:
    """Return unique, unfetched HTTPS landing pages; never bulk resource URLs."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0 or not path.is_file():
        return []
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        return []
    rows = payload.get("source_pages", [])
    if not isinstance(rows, list):
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("fetched") is True or row.get("policy_allowed") is not True:
            continue
        value = row.get("canonical_url") or row.get("url")
        if not isinstance(value, str) or value != value.strip():
            continue
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError:
            continue
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            continue
        if parsed.username or parsed.password:
            continue
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            continue
        canonical = value.split("#", 1)[0]
        if canonical not in seen:
            seen.add(canonical)
            urls.append(canonical)
            if len(urls) >= limit:
                break
    return urls
