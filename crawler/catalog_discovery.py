#!/usr/bin/env python3
"""Discover whole public data catalogs and preserve dataset/resource manifests.

This is deliberately separate from page crawling: one catalog can expose
thousands of datasets, each with multiple bulk-download resources.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "catalogs.json"
MAX_RESPONSE_BYTES = 8_000_000
TIMEOUT_SECONDS = 25
USER_AGENT = "EverythingHubCatalogDiscovery/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"

# Explicit public CKAN API hosts. No user-supplied URLs are fetched.
SOURCES = (
    {"id": "us-data-gov", "name": "Data.gov", "base": "https://catalog.data.gov", "category": "government"},
    {"id": "canada-open-data", "name": "Open Government Canada", "base": "https://open.canada.ca/data", "category": "government"},
    {"id": "australia-data-gov", "name": "Data.gov.au", "base": "https://data.gov.au", "api_path": "/data/api/3/action/package_search", "category": "government"},
    {"id": "swiss-open-data", "name": "opendata.swiss", "base": "https://opendata.swiss", "category": "government"},
)

def _request_json(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("catalog API URL must use HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise ValueError(f"catalog API returned HTTP {response.status}")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("catalog API response exceeded size limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog API response was not a JSON object")
    return payload

def _clean_text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]

def normalize_dataset(dataset: dict[str, Any], source: dict[str, str]) -> dict[str, Any] | None:
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
    landing = source["base"].rstrip("/") + "/dataset/" + urllib.parse.quote(name, safe="")
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
    }

def discover_source(source: dict[str, str], rows: int, max_pages: int, pause: float = 0.2) -> dict[str, Any]:
    datasets: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for page in range(max_pages):
        params = urllib.parse.urlencode({
            "q": "*:*",
            "rows": rows,
            "start": page * rows,
            "sort": "metadata_modified desc",
        })
        url = source["base"].rstrip("/") + source.get("api_path", "/api/3/action/package_search") + "?" + params
        try:
            payload = _request_json(url)
            result = payload.get("result")
            if payload.get("success") is not True or not isinstance(result, dict):
                raise ValueError("CKAN package_search did not return success")
            batch = result.get("results")
            if not isinstance(batch, list):
                raise ValueError("CKAN package_search results were missing")
            for raw in batch:
                if not isinstance(raw, dict):
                    continue
                item = normalize_dataset(raw, source)
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
        "id": source["id"],
        "name": source["name"],
        "base_url": source["base"],
        "api_path": source.get("api_path", "/api/3/action/package_search"),
        "category": source["category"],
        "datasets_discovered": len(datasets),
        "resources_discovered": sum(item["resource_count"] for item in datasets),
        "pages_requested": page + 1 if max_pages else 0,
        "errors": errors,
        "datasets": datasets,
    }

def build_catalog(rows: int = 250, max_pages: int = 20) -> dict[str, Any]:
    if not 1 <= rows <= 1000:
        raise ValueError("rows must be between 1 and 1000")
    if not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")
    results = [discover_source(source, rows, max_pages) for source in SOURCES]
    datasets = [item for result in results for item in result["datasets"]]
    return {
        "schema_version": 1,
        "strategy": "catalog-first",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "catalog_count": len(results),
        "datasets_discovered": len(datasets),
        "resources_discovered": sum(item["resource_count"] for item in datasets),
        "catalogs": [{key: value for key, value in result.items() if key != "datasets"} for result in results],
        "datasets": datasets,
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=250)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()
    catalog = build_catalog(args.rows, args.max_pages)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({key: catalog[key] for key in ("catalog_count", "datasets_discovered", "resources_discovered", "updated_at")}, indent=2))

if __name__ == "__main__":
    main()
