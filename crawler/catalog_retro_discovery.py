#!/usr/bin/env python3
"""Build host discovery hints and signal-filtered page-analysis work items.

This script reuses the existing page index. It does not create catalog
definitions. A page becomes a work item only when its indexed title, text, or
URL contains a discovery hint. Actual catalog definitions are created only by
catalog_page_analysis.py after page-content evidence is evaluated.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data" / "index.json"
DEFAULT_OUTPUT = ROOT / "data" / "host-catalog-candidates.json"
DEFAULT_PAGE_OUTPUT = ROOT / "data" / "page-analysis-work-items.json.gz"

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
    page_work_items = []
    seen_page_urls = set()
    duplicate_page_rows = 0
    grouped = defaultdict(
        lambda: {
            "source_pages": 0,
            "hint_pages": 0,
            "examples": [],
            "hint_counts": defaultdict(int),
            "lanes": defaultdict(int),
        }
    )

    for page in index.get("pages", []):
        if not isinstance(page, dict):
            continue
        url = page.get("url")
        host = host_key(url) if isinstance(url, str) else None
        if not host:
            continue
        if url in seen_page_urls:
            duplicate_page_rows += 1
            continue
        seen_page_urls.add(url)

        item = grouped[host]
        item["source_pages"] += 1
        lane = page.get("lane")
        if isinstance(lane, str) and lane:
            item["lanes"][lane] += 1

        text = " ".join(str(page.get(k) or "") for k in ("title", "description", "text"))[:2500]
        matched = [name for name, pattern in HINTS.items() if pattern.search(text) or pattern.search(url)]
        title = str(page.get("title") or "")[:240]

        # This is an analysis work item, not a catalog candidate or definition.
        # Pages with no discovery signal remain in the full page-analysis pass,
        # but do not get a placeholder catalog/profile.
        if matched:
            page_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
            page_work_items.append(
                {
                    "work_item_id": "page-analysis:" + page_digest,
                    "source_page_id": page_digest,
                    "source_url": url[:2048],
                    "host": host,
                    "title": title,
                    "lane": lane if isinstance(lane, str) else None,
                    "catalog_signals": matched,
                    "analysis_status": "pending_page_content_analysis",
                }
            )
            item["hint_pages"] += 1

        for name in matched:
            item["hint_counts"][name] += 1
        if len(item["examples"]) < 8:
            item["examples"].append(
                {
                    "url": url[:2048],
                    "title": title,
                    "lane": lane if isinstance(lane, str) else None,
                    "catalog_hints": matched,
                }
            )

    hosts = []
    for host, item in grouped.items():
        hints = dict(sorted(item["hint_counts"].items(), key=lambda pair: (-pair[1], pair[0])))
        lanes = dict(sorted(item["lanes"].items(), key=lambda pair: (-pair[1], pair[0])))
        hosts.append(
            {
                "host": host,
                "source_pages": item["source_pages"],
                "catalog_hint_pages": item["hint_pages"],
                "hint_counts": hints,
                "lanes": lanes,
                "examples": item["examples"],
                "status": "candidate_needs_catalog_probe",
            }
        )
    hosts.sort(key=lambda x: (-sum(x["hint_counts"].values()), -x["source_pages"], x["host"]))

    unique_source_pages = len(seen_page_urls)
    pages_with_signals = len(page_work_items)
    signal_references = sum(len(item["catalog_signals"]) for item in page_work_items)
    return {
        "schema_version": 2,
        "strategy": "host-discovery-with-signal-filtered-page-analysis-work-items",
        "source_index_pages": int(index.get("total_pages", len(index.get("pages", [])))),
        "hosts_discovered": len(hosts),
        "host_candidates": hosts,
        "page_analysis_work_items": page_work_items,
        "metrics": {
            "unique_source_pages": unique_source_pages,
            "duplicate_source_page_rows_skipped": duplicate_page_rows,
            "page_analysis_work_items_created": len(page_work_items),
            "pages_as_discovery_work_items": len(page_work_items),
            "pages_with_catalog_signals": pages_with_signals,
            "page_catalog_signal_references": signal_references,
            # This host-hint pass does not classify pages as catalogs.
            "unique_catalog_candidates": 0,
            "verified_page_catalogs": 0,
            "source_pages_grouped": sum(x["source_pages"] for x in hosts),
            "hosts_with_catalog_hints": sum(bool(x["hint_counts"]) for x in hosts),
            "candidate_hosts": len(hosts),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--page-output", type=Path, default=DEFAULT_PAGE_OUTPUT)
    args = parser.parse_args()

    index = load_index(args.index)
    result = build_candidates(index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    host_result = dict(result)
    host_result.pop("page_analysis_work_items", None)
    temp.write_text(json.dumps(host_result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(args.output)

    # Keep this as a compact work queue. It contains no catalog IDs and is never
    # consumed by the child-record expansion workflow.
    page_result = {
        "schema_version": 1,
        "status": "analysis_work_items_not_catalogs",
        "strategy": "signal-filtered-source-page-analysis-work-items",
        "source_index_pages": result["source_index_pages"],
        "metrics": {
            key: value
            for key, value in result["metrics"].items()
            if key
            in {
                "unique_source_pages",
                "duplicate_source_page_rows_skipped",
                "page_analysis_work_items_created",
                "pages_as_discovery_work_items",
                "pages_with_catalog_signals",
                "page_catalog_signal_references",
                "unique_catalog_candidates",
                "verified_page_catalogs",
            }
        },
        "items": result["page_analysis_work_items"],
    }
    args.page_output.parent.mkdir(parents=True, exist_ok=True)
    page_temp = args.page_output.with_suffix(args.page_output.suffix + ".tmp")
    page_payload = json.dumps(page_result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.page_output.suffix == ".gz":
        page_temp.write_bytes(gzip.compress(page_payload, mtime=0))
    else:
        page_temp.write_bytes(page_payload)
    page_temp.replace(args.page_output)

    print(json.dumps({"source_index_pages": result["source_index_pages"], **result["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
