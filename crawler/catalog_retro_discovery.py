#!/usr/bin/env python3
"""Build a host-by-host catalog-discovery backlog from the existing page index.

This is a retroactive pass: it reuses already indexed pages and does not refetch
them. It groups all records by host and identifies pages that may expose
collections, directories, catalogs, APIs, feeds, or registries.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data" / "index.json"
DEFAULT_OUTPUT = ROOT / "data" / "host-catalog-candidates.json"

HINTS = {
    "sitemap": re.compile(r"sitemap|site-map", re.I),
    "catalog": re.compile(r"catalog|collection|directory|index|archive", re.I),
    "api": re.compile(r"\bapi\b|developer|openapi|swagger", re.I),
    "feed": re.compile(r"rss|atom|feed|export|download|bulk", re.I),
    "registry": re.compile(r"registry|register|database|repository|dataset", re.I),
    "category": re.compile(r"category|categories|department|products|services|locations", re.I),
}

def host_key(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname.lower().rstrip(".")
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return None

def build_candidates(index: dict) -> dict:
    page_seeds = []
    grouped = defaultdict(lambda: {"source_pages": 0, "hint_pages": 0, "examples": [], "hint_counts": defaultdict(int), "lanes": defaultdict(int)})
    for page in index.get("pages", []):
        if not isinstance(page, dict):
            continue
        url = page.get("url")
        host = host_key(url) if isinstance(url, str) else None
        if not host:
            continue
        item = grouped[host]
        item["source_pages"] += 1
        lane = page.get("lane")
        if isinstance(lane, str) and lane:
            item["lanes"][lane] += 1
        text = " ".join(str(page.get(k) or "") for k in ("title", "description", "text"))[:2500]
        matched = [name for name, pattern in HINTS.items() if pattern.search(text) or pattern.search(url)]
        page_seeds.append({
            "page_id": __import__("hashlib").sha256(url.encode("utf-8")).hexdigest()[:24],
            "url": url[:2048],
            "host": host,
            "title": str(page.get("title") or "")[:240],
            "lane": lane if isinstance(lane, str) else None,
            "catalog_signals": matched,
            "status": "needs_page_catalog_extraction",
        })
        if matched:
            item["hint_pages"] += 1
        for name in matched:
            item["hint_counts"][name] += 1
        if len(item["examples"]) < 8:
            item["examples"].append({
                "url": url[:2048],
                "title": str(page.get("title") or "")[:240],
                "lane": lane if isinstance(lane, str) else None,
                "catalog_hints": matched,
            })
    hosts = []
    for host, item in grouped.items():
        hints = dict(sorted(item["hint_counts"].items(), key=lambda pair: (-pair[1], pair[0])))
        lanes = dict(sorted(item["lanes"].items(), key=lambda pair: (-pair[1], pair[0])))
        hosts.append({
            "host": host,
            "source_pages": item["source_pages"],
            "catalog_hint_pages": item["hint_pages"],
            "hint_counts": hints,
            "lanes": lanes,
            "examples": item["examples"],
            "status": "candidate_needs_catalog_probe",
        })
    hosts.sort(key=lambda x: (-sum(x["hint_counts"].values()), -x["source_pages"], x["host"]))
    return {
        "schema_version": 1,
        "strategy": "retroactive-host-catalog-discovery",
        "source_index_pages": int(index.get("total_pages", len(index.get("pages", [])))),
        "hosts_discovered": len(hosts),
        "host_candidates": hosts,
        "page_catalog_seeds": page_seeds,
        "metrics": {
            "pages_as_discovery_seeds": len(page_seeds),
            "pages_with_catalog_signals": sum(bool(x["catalog_signals"]) for x in page_seeds),
            "page_catalog_signal_references": sum(len(x["catalog_signals"]) for x in page_seeds),
            "source_pages_grouped": sum(x["source_pages"] for x in hosts),
            "hosts_with_catalog_hints": sum(bool(x["hint_counts"]) for x in hosts),
            "candidate_hosts": len(hosts),
        },
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    index = load_index(args.index)
    result = build_candidates(index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps({"source_index_pages": result["source_index_pages"], **result["metrics"]}, indent=2))

if __name__ == "__main__":
    main()
