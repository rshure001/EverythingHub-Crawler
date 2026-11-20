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
from catalog_source_policy import SourcePolicyError, request_source_bytes

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
        "errors": errors,
        "catalogs": catalogs,
        "datasets": datasets,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--allow-api", action="store_true", help="Explicitly enable API requests only after source-policy approval.")
    args = parser.parse_args()
    if not 0 <= args.pause <= 5:
        parser.error("pause must be between 0 and 5 seconds")
    if not args.allow_api:
        print(json.dumps({"skipped": True, "reason": "Open Library API is not approved for bulk catalog backend use; previous snapshot preserved."}, indent=2))
        return
    result = discover(args.pause, allow_api=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.output.suffix == ".gz":
        temp.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temp.write_bytes(payload)
    temp.replace(args.output)
    print(json.dumps({
        "catalogs_discovered": result["catalog_count"],
        "catalog_records_discovered": result["datasets_discovered"],
        "subjects_with_errors": len(result["errors"]),
    }, indent=2))


if __name__ == "__main__":
    main()
