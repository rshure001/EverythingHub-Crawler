#!/usr/bin/env python3
"""Adapt discovered CKAN datasets into the shared universal catalog contract.

Output is a normalized, deduplicated staging artifact. It is not advertised as
production-indexed until a separate database/search ingestion step succeeds.
"""
from __future__ import annotations
import argparse, gzip, json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from catalog_model import catalog_metrics, normalize_catalog, normalize_record

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "catalogs.json.gz"
OUTPUT = ROOT / "data" / "universal-catalog-records.json.gz"
OPENLIBRARY_INPUT = ROOT / "data" / "openlibrary-catalogs.json.gz"

def adapt(manifest: dict[str, Any]) -> dict[str, Any]:
    catalogs, records, source_pages = [], [], []
    seen_records: set[str] = set()
    for source in manifest.get("catalogs", []):
        source_id = source.get("id")
        if not source_id:
            continue
        base = source.get("base_url")
        if not isinstance(base, str) or not base.startswith("https://"):
            continue
        datasets = [x for x in manifest.get("datasets", []) if x.get("catalog_id") == source_id]
        errors = source.get("errors") or []
        catalog_type = source.get("catalog_type") or "open_data"
        source_terms_status = source.get("source_terms_status") or (
            "restricted_pending_permission" if catalog_type == "library_archive" else "unreviewed"
        )
        source_terms_url = source.get("source_terms_url") or (
            "https://openlibrary.org/developers/api" if catalog_type == "library_archive" else ""
        )
        catalog_policy_approved = (
            source_terms_status in ("reviewed", "reviewed_per_dataset")
            and catalog_type != "library_archive"
        )
        catalog = normalize_catalog({
            "source_id": source_id,
            "title": source.get("name") or source_id,
            "description": source.get("description") or "Public collection discovered through a machine-readable catalog interface.",
            "catalog_type": catalog_type,
            "subject": source.get("category") or "open data",
            "publisher": source.get("name") or "",
            "source_url": source.get("source_url") or base.rstrip("/") + source.get("api_path", "/api/3/action/package_search"),
            "access_method": source.get("access_method") or "api",
            "coverage_status": source.get("coverage_status") or ("partial" if errors or datasets else "unknown"),
            "record_count": len(datasets),
            "last_discovered_at": manifest.get("updated_at") or "",
        })
        catalog["source_terms_url"] = source_terms_url
        catalog["source_terms_status"] = source_terms_status
        catalog["source_terms_reviewed_at"] = source.get("source_terms_reviewed_at") or ""
        catalog["attribution_required"] = source.get("attribution_required", "per_dataset")
        catalog["public_search_allowed"] = catalog_policy_approved
        catalogs.append(catalog)
        for dataset in datasets:
            landing = dataset.get("landing_url")
            if not isinstance(landing, str):
                continue
            if not landing.startswith("https://"):
                raise ValueError("dataset landing URLs must use HTTPS")
            license_info = dataset.get("license") or {}
            if not isinstance(license_info, dict):
                license_info = {}
            license_present = bool(license_info.get("url") or license_info.get("id"))
            public_search_allowed = catalog_policy_approved and license_present
            attributes = dataset.get("attributes") or {
                "organization": dataset.get("organization") or "",
                "tags": dataset.get("tags") or [],
                "resources": dataset.get("resources") or [],
                "resource_count": int(dataset.get("resource_count") or 0),
                "metadata_modified": dataset.get("metadata_modified") or "",
            }
            if not isinstance(attributes, dict):
                attributes = {}
            attributes = dict(attributes)
            attributes.update({
                "license": license_info,
                "source_terms_url": dataset.get("source_terms_url") or source_terms_url,
                "source_terms_status": source_terms_status,
                "source_terms_reviewed_at": dataset.get("source_terms_reviewed_at") or source.get("source_terms_reviewed_at") or "",
                "attribution_required": dataset.get("attribution_required", catalog.get("attribution_required")),
                "rights_status": "license_metadata_present" if license_present else "review_required",
                "public_search_allowed": public_search_allowed,
            })
            raw = {
                "source_key": dataset.get("dataset_id") or dataset.get("id"),
                "record_type": dataset.get("record_type") or "dataset",
                "title": dataset.get("title") or dataset.get("dataset_id"),
                "description": dataset.get("description") or "",
                "source_url": landing,
                "canonical_entity_key": dataset.get("canonical_entity_key") or ("dataset:" + str(dataset.get("id") or dataset.get("dataset_id"))),
                "subject": dataset.get("subject") or ", ".join(dataset.get("tags") or [])[:200],
                "attributes": attributes,
                "last_seen_at": manifest.get("updated_at") or "",
            }
            record = normalize_record(raw, catalog["catalog_id"])
            record["attributes"] = attributes
            if record["record_id"] not in seen_records:
                seen_records.add(record["record_id"])
                records.append(record)
                source_pages.append({
                    "url": landing,
                    "canonical_url": landing,
                    "fetched": False,
                    "policy_allowed": public_search_allowed,
                    "source_terms_status": source_terms_status,
                    "record_id": record["record_id"],
                })
    metrics = catalog_metrics(catalogs, records, source_pages)
    metrics["production_eligible_records"] = sum(
        1 for record in records if (record.get("attributes") or {}).get("public_search_allowed") is True
    )
    metrics["restricted_records"] = len(records) - metrics["production_eligible_records"]
    metrics["approved_source_page_references"] = sum(1 for page in source_pages if page.get("policy_allowed") is True)
    metrics["unique_records_by_type"] = dict(sorted(Counter(record["record_type"] for record in records).items()))
    return {
        "schema_version": 1,
        "strategy": "universal-catalog-staging",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "production_indexed": False,
        "metrics": metrics,
        "catalogs": catalogs,
        "records": records,
        "source_pages": source_pages,
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    raw = gzip.decompress(args.input.read_bytes()) if args.input.suffix == ".gz" else args.input.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    if OPENLIBRARY_INPUT.exists():
        extra_raw = gzip.decompress(OPENLIBRARY_INPUT.read_bytes())
        extra = json.loads(extra_raw.decode("utf-8"))
        manifest = {
            **manifest,
            "catalogs": manifest.get("catalogs", []) + extra.get("catalogs", []),
            "datasets": manifest.get("datasets", []) + extra.get("datasets", []),
        }
    result = adapt(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.output.suffix == ".gz":
        temp.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temp.write_bytes(payload)
    temp.replace(args.output)
    print(json.dumps(result["metrics"], indent=2))

if __name__ == "__main__":
    main()
