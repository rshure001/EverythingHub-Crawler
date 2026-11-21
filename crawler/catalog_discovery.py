#!/usr/bin/env python3
"""Discover whole public data catalogs and preserve dataset/resource manifests.

This is deliberately separate from page crawling: one catalog can expose
thousands of datasets, each with multiple bulk-download resources.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from catalog_source_policy import policy_for_host, request_source_bytes

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "catalogs.json.gz"
MAX_RESPONSE_BYTES = 8_000_000
TIMEOUT_SECONDS = 25
USER_AGENT = "EverythingHubCatalogDiscovery/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"

# Explicit public CKAN API hosts with reviewed, per-dataset rights handling.
SOURCES = (
    {
        "id": "us-data-gov", "name": "Data.gov",
        "base": "https://catalog.data.gov", "landing_base": "https://catalog.data.gov",
        "api_path": "/api/3/action/package_search", "category": "government",
    },
    {
        "id": "canada-open-data", "name": "Open Government Canada",
        "base": "https://open.canada.ca", "landing_base": "https://open.canada.ca/data/en",
        "api_path": "/data/en/api/3/action/package_search", "category": "government",
    },
    {
        "id": "swiss-open-data", "name": "opendata.swiss",
        "base": "https://opendata.swiss", "landing_base": "https://opendata.swiss",
        "api_path": "/api/3/action/package_search", "category": "government",
    },
)
EXCLUDED_SOURCES = (
    {"id": "australia-data-gov", "name": "Data.gov.au",
     "reason": "Source terms and dataset-level reuse conditions have not been reviewed."},
)

def _request_json(url: str) -> dict[str, Any]:
    raw, status, _headers, _policy = request_source_bytes(
        url, purpose="discovery", max_bytes=MAX_RESPONSE_BYTES,
        timeout=TIMEOUT_SECONDS, accept="application/json",
    )
    if status != 200:
        raise ValueError(f"catalog API returned HTTP {status}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog API response was not a JSON object")
    return payload

def _clean_text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]

def normalize_dataset(dataset: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    name = _clean_text(dataset.get("name") or dataset.get("id"), 300)
    title = _clean_text(dataset.get("title") or name, 500)
    if not name or not title:
        return None
    resources = []
    for resource in dataset.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        url = resource.get("url")
        parsed = urllib.parse.urlsplit(str(url or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            continue
        resources.append({
            "name": _clean_text(resource.get("name") or resource.get("description"), 300),
            "format": _clean_text(resource.get("format"), 40).upper(),
            "url": url,
            "bytes": resource.get("size") if isinstance(resource.get("size"), int) else None,
        })
    landing_base = source.get("landing_base") or source["base"]
    landing = landing_base.rstrip("/") + "/dataset/" + urllib.parse.quote(name, safe="")
    license_info = {
        "id": _clean_text(dataset.get("license_id"), 160),
        "title": _clean_text(dataset.get("license_title"), 240),
        "url": _clean_text(dataset.get("license_url"), 1000),
        "rights": _clean_text(dataset.get("rights") or dataset.get("access_and_use"), 1000),
        "is_open": dataset.get("isopen") if isinstance(dataset.get("isopen"), bool) else None,
    }
    return {
        "id": source["id"] + ":" + name,
        "catalog_id": source["id"],
        "catalog_name": source["name"],
        "category": source["category"],
        "dataset_id": name,
        "title": title,
        "description": _clean_text(dataset.get("notes"), 4000),
        "organization": _clean_text((dataset.get("organization") or {}).get("title"), 300),
        "tags": [_clean_text(t.get("name"), 80) for t in (dataset.get("tags") or []) if isinstance(t, dict)][:30],
        "landing_url": landing,
        "resources": resources,
        "resource_count": len(resources),
        "metadata_modified": _clean_text(dataset.get("metadata_modified"), 80),
        "license": license_info,
        "source_terms_url": source.get("source_terms_url", ""),
        "source_terms_status": source.get("source_terms_status", "review_required"),
        "source_terms_reviewed_at": source.get("source_terms_reviewed_at", ""),
        "attribution_required": source.get("attribution_required", "per_dataset"),
        "metadata_only": True,
    }

def discover_source(source: dict[str, Any], rows: int, max_pages: int, pause: float = 0.0) -> dict[str, Any]:
    datasets: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    host = urllib.parse.urlsplit(source["base"]).hostname or ""
    policy = policy_for_host(host)
    if not policy or policy.get("discovery_allowed") is not True:
        return {
            "id": source["id"], "name": source["name"],
            "base_url": source["base"], "api_path": source["api_path"],
            "source_url": source["base"].rstrip("/") + source["api_path"],
            "category": source["category"],
            "source_terms_url": (policy or {}).get("terms_url", ""),
            "source_terms_status": (policy or {}).get("terms_status", "unreviewed"),
            "datasets_discovered": 0, "resources_discovered": 0,
            "pages_requested": 0,
            "errors": ["source terms do not authorize catalog discovery"],
            "datasets": [],
        }
    pages_requested = 0
    for page in range(max_pages):
        params = urllib.parse.urlencode({
            "q": "*:*", "rows": rows, "start": page * rows,
            "sort": "metadata_modified desc",
        })
        url = source["base"].rstrip("/") + source["api_path"] + "?" + params
        try:
            payload = _request_json(url)
            pages_requested += 1
            result = payload.get("result")
            if payload.get("success") is not True or not isinstance(result, dict):
                raise ValueError("CKAN package_search did not return success")
            batch = result.get("results")
            if not isinstance(batch, list):
                raise ValueError("CKAN package_search results were missing")
            source_meta = {
                **source,
                "source_terms_url": policy.get("terms_url", ""),
                "source_terms_status": policy.get("terms_status", "reviewed_per_dataset"),
                "source_terms_reviewed_at": policy.get("reviewed_at", ""),
                "attribution_required": policy.get("attribution_required", "per_dataset"),
            }
            for raw in batch:
                if not isinstance(raw, dict):
                    continue
                item = normalize_dataset(raw, source_meta)
                if item and item["id"] not in seen:
                    seen.add(item["id"])
                    datasets.append(item)
            count = result.get("count")
            if not batch or (isinstance(count, int) and (page + 1) * rows >= count):
                break
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            errors.append(f"page {page + 1}: {type(exc).__name__}: {str(exc)[:240]}")
            break
        if pause:
            time.sleep(pause)
    return {
        "id": source["id"], "name": source["name"],
        "base_url": source["base"], "api_path": source["api_path"],
        "source_url": source["base"].rstrip("/") + source["api_path"],
        "category": source["category"],
        "source_terms_url": policy.get("terms_url", ""),
        "source_terms_status": policy.get("terms_status", "reviewed_per_dataset"),
        "source_terms_reviewed_at": policy.get("reviewed_at", ""),
        "attribution_required": policy.get("attribution_required", "per_dataset"),
        "metadata_only": True,
        "per_dataset_rights_required": bool(policy.get("per_dataset_rights_required", True)),
        "datasets_discovered": len(datasets),
        "resources_discovered": sum(item["resource_count"] for item in datasets),
        "pages_requested": pages_requested,
        "errors": errors, "datasets": datasets,
    }

def build_catalog(rows: int = 250, max_pages: int = 40) -> dict[str, Any]:
    if not 1 <= rows <= 1000:
        raise ValueError("rows must be between 1 and 1000")
    if not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")
    results = [discover_source(source, rows, max_pages) for source in SOURCES]
    datasets = [item for result in results for item in result["datasets"]]
    return {
        "schema_version": 1,
        "strategy": "catalog-first-policy-gated",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "catalog_count": len(results),
        "datasets_discovered": len(datasets),
        "resources_discovered": sum(item["resource_count"] for item in datasets),
        "excluded_sources": list(EXCLUDED_SOURCES),
        "catalogs": [{key: value for key, value in result.items() if key != "datasets"} for result in results],
        "datasets": datasets,
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=250)
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()
    catalog = build_catalog(args.rows, args.max_pages)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.output.suffix == ".gz":
        temporary.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temporary.write_bytes(payload)
    temporary.replace(args.output)
    print(json.dumps({key: catalog[key] for key in ("catalog_count", "datasets_discovered", "resources_discovered", "updated_at")}, indent=2))

if __name__ == "__main__":
    main()
