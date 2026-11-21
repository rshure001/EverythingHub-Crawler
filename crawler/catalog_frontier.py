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

 
def plan_catalog_seed_enqueue(
    seed_urls: list[str],
    *,
    indexed_urls: set[str],
    seen_urls: set[str],
    queued_urls: set[str],
    queue_remaining: int,
) -> tuple[list[str], dict[str, int]]:
    """Plan catalog landing-page additions and explain every skipped candidate.

    The caller supplies URLs already validated by load_catalog_seed_urls.
    This function is pure: it does not mutate the frontier or any input set.
    """
    if queue_remaining < 0:
        raise ValueError("queue_remaining must be non-negative")

    additions: list[str] = []
    unique: set[str] = set()
    metrics = {
        "candidates": 0,
        "duplicates": 0,
        "already_indexed": 0,
        "already_queued": 0,
        "already_seen": 0,
        "queue_full": 0,
        "queued": 0,
    }
    for url in seed_urls:
        if not isinstance(url, str) or not url:
            continue
        if url in unique:
            metrics["duplicates"] += 1
            continue
        unique.add(url)
        metrics["candidates"] += 1
        if url in indexed_urls:
            metrics["already_indexed"] += 1
        elif url in queued_urls:
            metrics["already_queued"] += 1
        elif url in seen_urls:
            metrics["already_seen"] += 1
        elif len(additions) >= queue_remaining:
            metrics["queue_full"] += 1
        else:
            additions.append(url)

    metrics["queued"] = len(additions)
    return additions, metrics
