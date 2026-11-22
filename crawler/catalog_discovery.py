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
DEFAULT_MAX_PAGES = 40
MAX_PAGES_PER_SOURCE = 80
SORT_ORDER = "id asc"
USER_AGENT = "EverythingHubCatalogDiscovery/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"

# Explicit public CKAN API hosts with reviewed, per-dataset rights handling.
SOURCES = (
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
    {"id": "us-data-gov", "name": "Data.gov",
     "reason": "The legacy CKAN endpoint now returns HTTP 404. The replacement v4 API requires a free personal API key; DEMO_KEY is explicitly unsuitable for automated queries. Keep excluded until a dedicated v4 adapter and configured key are available."},
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
    # Never stage private or inactive records from a public catalog adapter.
    if dataset.get("private") is True:
        return None
    state = _clean_text(dataset.get("state"), 40).casefold()
    if state and state != "active":
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
            "description": _clean_text(resource.get("description"), 1000),
            "format": _clean_text(resource.get("format"), 40).upper(),
            "media_type": _clean_text(resource.get("mimetype") or resource.get("media-type"), 160),
            "rights": _clean_text(resource.get("rights"), 800),
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
        "private": dataset.get("private") if isinstance(dataset.get("private"), bool) else None,
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

def discover_source(
    source: dict[str, Any],
    rows: int,
    max_pages: int,
    pause: float = 0.0,
    *,
    start_offset: int = 0,
    previous_datasets: list[dict[str, Any]] | None = None,
    scan_generation: int = 1,
    previous_available_dataset_count: int | None = None,
    previous_pages_requested_total: int = 0,
) -> dict[str, Any]:
    """Fetch one bounded page range and persist a resumable per-source cursor.

    Existing records remain visible while a new scan generation is incomplete.
    Once the source's full result set has been traversed, records not observed
    in that generation are pruned. This avoids both repeated first-page harvests
    and premature removal of records while a multi-run scan is in progress.
    """
    start_offset = max(0, int(start_offset or 0))
    scan_generation = max(1, int(scan_generation or 1))
    datasets_by_id: dict[str, dict[str, Any]] = {}
    for previous in previous_datasets or []:
        if not isinstance(previous, dict) or not previous.get("id"):
            continue
        item = dict(previous)
        item.setdefault("last_seen_generation", scan_generation)
        datasets_by_id[str(item["id"])] = item

    errors: list[str] = []
    seen_this_run: set[str] = set()
    records_added = 0
    records_updated = 0
    pages_requested = 0
    available_dataset_count = (
        previous_available_dataset_count
        if isinstance(previous_available_dataset_count, int)
        and not isinstance(previous_available_dataset_count, bool)
        and previous_available_dataset_count >= 0
        else None
    )
    host = urllib.parse.urlsplit(source["base"]).hostname or ""
    policy = policy_for_host(host)

    def finish(
        *,
        page_limit_applied: int,
        scan_complete: bool,
        next_start: int,
    ) -> dict[str, Any]:
        current = list(datasets_by_id.values())
        if scan_complete:
            current = [
                item for item in current
                if int(item.get("last_seen_generation") or 0) == scan_generation
            ]
            next_start = 0
        current.sort(key=lambda item: str(item.get("id") or ""))
        resources = sum(int(item.get("resource_count") or 0) for item in current)
        return {
            "id": source["id"],
            "name": source["name"],
            "base_url": source["base"],
            "api_path": source["api_path"],
            "source_url": source["base"].rstrip("/") + source["api_path"],
            "category": source["category"],
            "source_terms_url": (policy or {}).get("terms_url", ""),
            "source_terms_status": (policy or {}).get("terms_status", "unreviewed"),
            "source_terms_reviewed_at": (policy or {}).get("reviewed_at", ""),
            "attribution_required": (policy or {}).get("attribution_required", "per_dataset"),
            "metadata_only": True,
            "per_dataset_rights_required": bool((policy or {}).get("per_dataset_rights_required", True)),
            "datasets_discovered": len(current),
            "resources_discovered": resources,
            "available_dataset_count": available_dataset_count,
            "page_limit_applied": page_limit_applied,
            "pages_requested": pages_requested,
            "pages_requested_total": previous_pages_requested_total + pages_requested,
            "next_start": next_start,
            "scan_generation": scan_generation,
            "sort_order": SORT_ORDER,
            "scan_complete": scan_complete,
            "coverage_status": "complete" if scan_complete else "partial" if current else "unknown",
            "records_added_this_run": records_added,
            "records_updated_this_run": records_updated,
            "errors": errors,
            "datasets": current,
        }

    if not policy or policy.get("discovery_allowed") is not True:
        errors.append("source terms do not authorize catalog discovery")
        return finish(
            page_limit_applied=0,
            scan_complete=False,
            next_start=start_offset,
        )

    request_budget = policy.get("max_requests_per_run")
    if (
        not isinstance(request_budget, int)
        or isinstance(request_budget, bool)
        or request_budget <= 0
    ):
        errors.append("source policy has no valid per-run request budget")
        return finish(
            page_limit_applied=0,
            scan_complete=False,
            next_start=start_offset,
        )

    page_limit_applied = min(max_pages, request_budget, MAX_PAGES_PER_SOURCE)
    scan_complete = False
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for page in range(page_limit_applied):
        page_start = start_offset + page * rows
        params = urllib.parse.urlencode({
            "q": "*:*",
            "rows": rows,
            "start": page_start,
            "sort": SORT_ORDER,
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
            count = result.get("count")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                if start_offset > 0 and (
                    count < start_offset
                    or (
                        previous_available_dataset_count is not None
                        and count < previous_available_dataset_count
                    )
                ):
                    available_dataset_count = count
                    scan_generation += 1
                    previous_pages_requested_total = 0
                    errors.append("source result count changed below the saved cursor; restarting from offset zero")
                    return finish(
                        page_limit_applied=page_limit_applied,
                        scan_complete=False,
                        next_start=0,
                    )
                available_dataset_count = count
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
                if not item or item["id"] in seen_this_run:
                    continue
                seen_this_run.add(item["id"])
                item["last_seen_generation"] = scan_generation
                item["last_seen_at"] = observed_at
                if item["id"] in datasets_by_id:
                    if datasets_by_id[item["id"]] != item:
                        records_updated += 1
                else:
                    records_added += 1
                datasets_by_id[item["id"]] = item

            if not batch or (
                available_dataset_count is not None
                and page_start + rows >= available_dataset_count
            ):
                scan_complete = True
                break
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            errors.append(f"offset {page_start}: {type(exc).__name__}: {str(exc)[:240]}")
            break
        if pause and page + 1 < page_limit_applied:
            time.sleep(pause)

    next_start = start_offset + pages_requested * rows
    if available_dataset_count is not None and next_start >= available_dataset_count:
        scan_complete = True
    return finish(
        page_limit_applied=page_limit_applied,
        scan_complete=scan_complete,
        next_start=next_start,
    )


def build_catalog(
    rows: int = 250,
    max_pages: int = DEFAULT_MAX_PAGES,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not 1 <= rows <= 1000:
        raise ValueError("rows must be between 1 and 1000")
    if not 1 <= max_pages <= MAX_PAGES_PER_SOURCE:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES_PER_SOURCE}")

    previous = previous if isinstance(previous, dict) else {}
    previous_catalogs = {
        str(item.get("id")): item
        for item in previous.get("catalogs", [])
        if isinstance(item, dict) and item.get("id")
    }
    previous_datasets: dict[str, list[dict[str, Any]]] = {}
    for item in previous.get("datasets", []):
        if isinstance(item, dict) and item.get("catalog_id"):
            previous_datasets.setdefault(str(item["catalog_id"]), []).append(item)

    results = []
    for source in SOURCES:
        source_id = source["id"]
        old = previous_catalogs.get(source_id, {})
        old_generation = old.get("scan_generation")
        has_generation = isinstance(old_generation, int) and not isinstance(old_generation, bool)
        sort_changed = old.get("sort_order") != SORT_ORDER
        legacy_snapshot = not has_generation or sort_changed
        if old.get("scan_complete") is True and has_generation and not sort_changed:
            generation = int(old_generation) + 1
            start_offset = 0
            pages_requested_total = 0
        elif sort_changed or not has_generation:
            # The prior snapshot used metadata_modified ordering, or predates
            # persisted cursors. Restart at zero so offsets never cross sort
            # orders; old records remain until this new generation completes.
            generation = max(1, int(old_generation or 0) + (1 if has_generation else 0))
            start_offset = 0
            pages_requested_total = 0
        else:
            generation = max(1, int(old_generation))
            saved_start = old.get("next_start")
            if isinstance(saved_start, int) and not isinstance(saved_start, bool) and saved_start >= 0:
                start_offset = saved_start
            else:
                start_offset = max(0, int(old.get("pages_requested") or 0) * rows)
            pages_requested_total = max(
                0,
                int(old.get("pages_requested_total", old.get("pages_requested", 0)) or 0),
            )
            old_count = old.get("available_dataset_count")
            if (
                isinstance(old_count, int)
                and not isinstance(old_count, bool)
                and old_count >= 0
                and start_offset >= old_count
                and start_offset > 0
            ):
                generation += 1
                start_offset = 0
                pages_requested_total = 0

        prior_rows = [dict(item) for item in previous_datasets.get(source_id, [])]
        for item in prior_rows:
            if legacy_snapshot:
                # Existing records belong to the previous sort/generation and
                # are retained until the new full scan can safely replace them.
                item["last_seen_generation"] = max(0, int(old_generation or 0))
            else:
                item.setdefault("last_seen_generation", max(0, generation - 1))

        result = discover_source(
            source,
            rows,
            max_pages,
            start_offset=start_offset,
            previous_datasets=prior_rows,
            scan_generation=generation,
            previous_available_dataset_count=old.get("available_dataset_count"),
            previous_pages_requested_total=pages_requested_total,
        )
        results.append(result)

    datasets = [item for result in results for item in result["datasets"]]
    catalogs = [{key: value for key, value in result.items() if key != "datasets"} for result in results]
    return {
        "schema_version": 1,
        "strategy": "catalog-first-policy-gated-resumable",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "catalog_count": len(catalogs),
        "datasets_discovered": len(datasets),
        "resources_discovered": sum(int(item.get("resource_count") or 0) for item in datasets),
        "excluded_sources": list(EXCLUDED_SOURCES),
        "catalogs": catalogs,
        "datasets": datasets,
        "scan_progress": {
            item["id"]: {
                "scan_generation": item["scan_generation"],
                "next_start": item["next_start"],
                "available_dataset_count": item["available_dataset_count"],
                "scan_complete": item["scan_complete"],
                "pages_requested_this_run": item["pages_requested"],
                "pages_requested_total": item["pages_requested_total"],
                "records_added_this_run": item["records_added_this_run"],
                "records_updated_this_run": item["records_updated_this_run"],
                "coverage_status": item["coverage_status"],
            }
            for item in catalogs
        },
    }


def load_previous_manifest(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        decoded = gzip.decompress(raw) if path.suffix == ".gz" else raw
        payload = json.loads(decoded.decode("utf-8"))
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("previous catalog snapshot is unreadable; refusing to overwrite it") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("strategy") not in ("catalog-first-policy-gated", "catalog-first-policy-gated-resumable")
        or not isinstance(payload.get("catalogs"), list)
        or not isinstance(payload.get("datasets"), list)
    ):
        raise ValueError("previous catalog snapshot has an unsupported schema; refusing to overwrite it")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=250)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument("--fresh", action="store_true", help="Ignore the previous snapshot and restart catalog discovery from offset zero.")
    args = parser.parse_args()
    previous = None if args.fresh else load_previous_manifest(args.output)
    catalog = build_catalog(args.rows, args.max_pages, previous=previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.output.suffix == ".gz":
        temporary.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temporary.write_bytes(payload)
    temporary.replace(args.output)
    summary = {
        key: catalog[key]
        for key in ("catalog_count", "datasets_discovered", "resources_discovered", "updated_at")
    }
    summary["source_catalogs"] = [{
        "id": item["id"],
        "name": item["name"],
        "datasets_discovered": item["datasets_discovered"],
        "available_dataset_count": item.get("available_dataset_count"),
        "page_limit_applied": item.get("page_limit_applied", 0),
        "pages_requested": item["pages_requested"],
        "pages_requested_total": item["pages_requested_total"],
        "next_start": item["next_start"],
        "scan_generation": item["scan_generation"],
        "scan_complete": item["scan_complete"],
        "records_added_this_run": item["records_added_this_run"],
        "records_updated_this_run": item["records_updated_this_run"],
        "errors": item["errors"],
    } for item in catalog["catalogs"]]
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
