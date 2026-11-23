#!/usr/bin/env python3
"""Discover public Open Library subject catalogs as normalized source datasets."""
from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from catalog_source_policy import SourcePolicyError, policy_for_host, request_source_bytes

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "openlibrary-catalogs.json.gz"
BASE = "https://openlibrary.org"
SUBJECTS = (
    "art", "biography", "business", "computers", "cooking", "fantasy",
    "fiction", "history", "horror", "mathematics", "medicine", "music",
    "mystery", "philosophy", "poetry", "religion", "romance", "science",
    "science_fiction", "technology", "travel", "education",
)
LIMIT = 500
TIMEOUT = 20
USER_AGENT = "EverythingHubCatalogDiscovery/1.0 (+https://github.com/rshure001/EverythingHub-Crawler)"


def _source_policy() -> dict:
    return policy_for_host("openlibrary.org") or {}


def _source_terms_approved(policy: dict) -> bool:
    return (
        policy.get("discovery_allowed") is True
        and policy.get("terms_status") in ("reviewed", "reviewed_per_dataset")
        and bool(policy.get("reviewed_at"))
    )


def _empty_snapshot(reason: str, policy: dict, previous_records_excluded=None, previous_catalogs_excluded=None) -> dict:
    return {
        "schema_version": 1,
        "strategy": "openlibrary-restricted-source-exclusion",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_policy_status": policy.get("terms_status", "unreviewed"),
        "discovery_allowed": False,
        "reason": reason,
        "previous_snapshot_records_excluded": previous_records_excluded,
        "previous_snapshot_catalogs_excluded": previous_catalogs_excluded,
        "catalog_count": 0,
        "datasets_discovered": 0,
        "resources_discovered": 0,
        "errors": [],
        "catalogs": [],
        "datasets": [],
    }



def request_subject(slug: str) -> dict:
    url = f"{BASE}/subjects/{urllib.parse.quote(slug, safe='')}.json?limit={LIMIT}"
    raw, status, _headers, _policy = request_source_bytes(
        url, purpose="discovery", max_bytes=8_000_000,
        timeout=TIMEOUT, accept="application/json",
    )
    if status != 200:
        raise ValueError(f"Open Library returned HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("works"), list):
        raise ValueError("Open Library subject response missing works")
    return data


def discover(pause: float = 1.0, allow_api: bool = False) -> dict:
    if not allow_api:
        raise SourcePolicyError("Open Library API discovery is disabled pending approved bulk-use terms or an official dump workflow.")
    policy = _source_policy()
    if not _source_terms_approved(policy):
        return _empty_snapshot(
            "Source policy does not authorize Open Library bulk discovery; no API requests were made.",
            policy,
        )
    catalogs, datasets = [], []
    errors = []
    for index, slug in enumerate(SUBJECTS):
        catalog_id = "openlibrary-subject-" + slug
        api_path = f"/subjects/{slug}.json"
        try:
            data = request_subject(slug)
            works = data["works"]
            records = []
            for work in works:
                if not isinstance(work, dict):
                    continue
                key = work.get("key")
                if not isinstance(key, str) or not key.startswith("/works/"):
                    continue
                work_id = key.rsplit("/", 1)[-1]
                if not work_id.startswith("OL"):
                    continue
                landing = BASE + key
                authors = [
                    {"name": author.get("name", ""), "key": author.get("key", "")}
                    for author in (work.get("authors") or [])[:10]
                    if isinstance(author, dict)
                ]
                records.append({
                    "catalog_id": catalog_id,
                    "id": catalog_id + ":" + work_id,
                    "dataset_id": work_id,
                    "title": str(work.get("title") or work_id)[:500],
                    "description": "",
                    "landing_url": landing,
                    "record_type": "document",
                    "canonical_entity_key": "openlibrary:work:" + work_id,
                    "subject": slug.replace("_", " "),
                    "attributes": {
                        "authors": authors,
                        "first_publish_year": work.get("first_publish_year"),
                        "edition_count": work.get("edition_count"),
                        "cover_id": work.get("cover_id"),
                        "subjects": (work.get("subject") or [])[:30],
                        "openlibrary_key": key,
                    },
                    "resource_count": 0,
                })
            catalogs.append({
                "id": catalog_id,
                "name": "Open Library — " + slug.replace("_", " ").title(),
                "base_url": BASE,
                "api_path": api_path,
                "category": slug.replace("_", " "),
                "catalog_type": "library_archive",
                "coverage_status": "partial" if len(records) < int(data.get("work_count") or len(records)) else "observed",
                "datasets_discovered": len(records),
                "resources_discovered": 0,
                "work_count": data.get("work_count"),
                "errors": [],
            })
            datasets.extend(records)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            errors.append({"subject": slug, "error": type(exc).__name__, "detail": str(exc)[:240]})
            catalogs.append({
                "id": catalog_id, "name": "Open Library — " + slug,
                "base_url": BASE, "api_path": api_path,
                "category": slug, "catalog_type": "library_archive",
                "coverage_status": "unavailable", "datasets_discovered": 0,
                "resources_discovered": 0, "errors": [str(exc)[:240]],
            })
        if pause and index + 1 < len(SUBJECTS):
            time.sleep(pause)
    return {
        "schema_version": 1,
        "strategy": "openlibrary-public-subject-catalogs",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "catalog_count": len(catalogs),
        "datasets_discovered": len(datasets),
        "resources_discovered": 0,
        "source_policy_status": policy.get("terms_status", "unreviewed"),
        "discovery_allowed": True,
        "errors": errors,
        "catalogs": catalogs,
        "datasets": datasets,
    }


def _previous_snapshot_counts(path: Path) -> tuple[int | None, int | None]:
    if not path.exists():
        return 0, 0
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        previous = json.loads(raw.decode("utf-8"))
        return len(previous.get("datasets") or []), len(previous.get("catalogs") or [])
    except (OSError, ValueError, json.JSONDecodeError, gzip.BadGzipFile):
        return None, None


def _write_snapshot(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if path.suffix == ".gz":
        temp.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temp.write_bytes(payload)
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--allow-api", action="store_true", help="Explicitly enable API requests only after source-policy approval.")
    args = parser.parse_args()
    if not 0 <= args.pause <= 5:
        parser.error("pause must be between 0 and 5 seconds")

    policy = _source_policy()
    if not _source_terms_approved(policy):
        previous_records, previous_catalogs = _previous_snapshot_counts(args.output)
        result = _empty_snapshot(
            "Open Library source terms do not authorize bulk catalog discovery; prior snapshot excluded from current public artifacts.",
            policy,
            previous_records,
            previous_catalogs,
        )
        _write_snapshot(args.output, result)
        print(json.dumps({
            "skipped": True,
            "reason": result["reason"],
            "catalogs_discovered": 0,
            "catalog_records_discovered": 0,
            "previous_snapshot_records_excluded": previous_records,
            "previous_snapshot_catalogs_excluded": previous_catalogs,
        }, indent=2))
        return

    if not args.allow_api:
        print(json.dumps({"skipped": True, "reason": "Open Library API discovery remains disabled until explicitly enabled after source-policy approval; approved prior snapshot preserved."}, indent=2))
        return

    result = discover(args.pause, allow_api=True)
    _write_snapshot(args.output, result)
    print(json.dumps({
        "catalogs_discovered": result["catalog_count"],
        "catalog_records_discovered": result["datasets_discovered"],
        "subjects_with_errors": len(result["errors"]),
    }, indent=2))


if __name__ == "__main__":
    main()
