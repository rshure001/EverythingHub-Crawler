#!/usr/bin/env python3
"""Build a host-by-host catalog-discovery backlog from the existing page index.

This is a retroactive pass: it reuses already indexed pages and does not refetch
them. It groups all records by host and identifies pages that may expose
collections, directories, catalogs, APIs, feeds, or registries.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from index_store import load_index
from catalog_page_analysis import canonical_url

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data" / "index.json"
DEFAULT_OUTPUT = ROOT / "data" / "host-catalog-candidates.json"
DEFAULT_PAGE_OUTPUT = ROOT / "data" / "page-catalog-seeds.json.gz"
DEFAULT_PAGE_ANALYSIS_DIR = ROOT / "data" / "page-derived-catalogs"

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

def _catalog_relationships(page_analysis: dict | None) -> dict[str, list[dict]]:
    """Map each indexed page to real catalog candidates from the page analyzer."""
    if not isinstance(page_analysis, dict):
        return {}
    catalog_by_key = {}
    for catalog in page_analysis.get("catalogs", []):
        if not isinstance(catalog, dict):
            continue
        key = catalog.get("source_key")
        catalog_id = catalog.get("catalog_id")
        if isinstance(key, str) and isinstance(catalog_id, str):
            catalog_by_key[key] = {
                "catalog_id": catalog_id,
                "title": str(catalog.get("title") or "")[:240],
                "source_page_count": len(catalog.get("source_page_ids") or []),
                "record_count": len(catalog.get("record_ids") or []),
                "catalog_status": catalog.get("catalog_status", catalog.get("status", "candidate")),
            }
    relationships = {}
    for page in page_analysis.get("pages", []):
        if not isinstance(page, dict) or not isinstance(page.get("url"), str):
            continue
        normalized_url = canonical_url(page["url"])
        if not normalized_url:
            continue
        related = []
        for key in page.get("catalog_group_keys", []):
            catalog = catalog_by_key.get(key)
            if catalog and catalog not in related:
                related.append(catalog)
        relationships[normalized_url] = sorted(
            related, key=lambda item: (-item["source_page_count"], -item["record_count"], item["catalog_id"])
        )
    return relationships


def build_candidates(index: dict, page_analysis: dict | None = None) -> dict:
    page_seeds = []
    page_relationships = _catalog_relationships(page_analysis)
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
        page_digest = __import__("hashlib").sha256(url.encode("utf-8")).hexdigest()[:24]
        normalized_url = canonical_url(url)
        related_catalogs = page_relationships.get(normalized_url, [])
        related_catalog_ids = [item["catalog_id"] for item in related_catalogs]
        best_page_count = max((item["source_page_count"] for item in related_catalogs), default=0)
        best_record_count = max((item["record_count"] for item in related_catalogs), default=0)
        profile_status = "catalog_associated" if related_catalogs else "pending_catalog_discovery"
        coverage_status = (
            "meets_100_page_target" if best_page_count >= 100
            else "below_100_page_target" if related_catalogs
            else "not_yet_classified"
        )
        page_seeds.append({
            "page_id": page_digest,
            "page_profile_id": "page-catalog-profile:" + page_digest,
            "profile_type": "source_page_catalog_profile",
            "profile_status": profile_status,
            "related_catalog_ids": related_catalog_ids,
            "related_catalog_count": len(related_catalog_ids),
            "best_catalog_source_page_count": best_page_count,
            "best_catalog_record_count": best_record_count,
            "catalog_coverage_status": coverage_status,
            "discovery_seed_id": "page-seed:" + page_digest,
            "analysis_status": "catalogs_associated" if related_catalogs else "pending_expansion",
            "url": url[:2048],
            "host": host,
            "title": str(page.get("title") or "")[:240],
            "lane": lane if isinstance(lane, str) else None,
            "catalog_signals": matched,
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
            "page_catalog_profiles_created": len(page_seeds),
            "profiles_associated_with_catalogs": sum(bool(x["related_catalog_ids"]) for x in page_seeds),
            "profiles_pending_catalog_discovery": sum(not x["related_catalog_ids"] for x in page_seeds),
            "page_catalog_associations": sum(len(x["related_catalog_ids"]) for x in page_seeds),
            "profiles_with_catalogs_meeting_100_page_target": sum(x["catalog_coverage_status"] == "meets_100_page_target" for x in page_seeds),
            "profiles_with_catalogs_below_100_page_target": sum(x["catalog_coverage_status"] == "below_100_page_target" for x in page_seeds),
            "unique_catalog_candidates": len({catalog_id for x in page_seeds for catalog_id in x["related_catalog_ids"]}),
            "verified_page_catalogs": 0,
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
    parser.add_argument("--page-output", type=Path, default=DEFAULT_PAGE_OUTPUT)
    parser.add_argument("--page-analysis-dir", type=Path, default=DEFAULT_PAGE_ANALYSIS_DIR)
    args = parser.parse_args()
    index = load_index(args.index)
    page_analysis = None
    registry_path = args.page_analysis_dir / "registry.json.gz"
    pages_path = args.page_analysis_dir / "pages.json.gz"
    if registry_path.is_file() and pages_path.is_file():
        registry = json.loads(gzip.decompress(registry_path.read_bytes()).decode("utf-8"))
        pages_payload = json.loads(gzip.decompress(pages_path.read_bytes()).decode("utf-8"))
        page_analysis = {"catalogs": registry.get("catalogs", []), "pages": pages_payload.get("pages", [])}
    result = build_candidates(index, page_analysis=page_analysis)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    host_result = dict(result)
    host_result.pop("page_catalog_seeds", None)
    temp.write_text(json.dumps(host_result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(args.output)
    page_result = {
        "schema_version": 1,
        "strategy": "one-persistent-page-catalog-profile-per-indexed-page",
        "source_index_pages": result["source_index_pages"],
        "metrics": {
            key: value for key, value in result["metrics"].items()
            if key.startswith("pages_") or key.startswith("profiles_")
            or key.startswith("page_catalog_")
            or key in ("unique_catalog_candidates", "verified_page_catalogs")
        },
        "pages": result["page_catalog_seeds"],
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
