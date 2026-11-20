#!/usr/bin/env python3
"""Adapt discovered CKAN datasets into the shared universal catalog contract.

Output is a normalized, deduplicated staging artifact. It is not advertised as
production-indexed until a separate database/search ingestion step succeeds.
"""
from __future__ import annotations
import argparse, gzip, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from catalog_model import catalog_metrics, normalize_catalog, normalize_record

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "catalogs.json.gz"
OUTPUT = ROOT / "data" / "universal-catalog-records.json.gz"

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
        catalog = normalize_catalog({
            "source_id": source_id,
            "title": source.get("name") or source_id,
            "description": "Public open-data dataset collection discovered through its CKAN API.",
            "catalog_type": "open_data",
            "subject": source.get("category") or "open data",
            "publisher": source.get("name") or "",
            "source_url": base.rstrip("/") + source.get("api_path", "/api/3/action/package_search"),
            "access_method": "api",
            "coverage_status": "partial" if errors or datasets else "unknown",
            "record_count": len(datasets),
            "last_discovered_at": manifest.get("updated_at") or "",
        })
        catalogs.append(catalog)
        for dataset in datasets:
            landing = dataset.get("landing_url")
            if not isinstance(landing, str):
                continue
            if not landing.startswith("https://"):
                raise ValueError("dataset landing URLs must use HTTPS")
            raw = {
                "source_key": dataset.get("dataset_id") or dataset.get("id"),
                "record_type": "dataset",
                "title": dataset.get("title") or dataset.get("dataset_id"),
                "description": dataset.get("description") or "",
                "source_url": landing,
                "canonical_entity_key": "dataset:" + str(dataset.get("id") or dataset.get("dataset_id")),
                "subject": ", ".join(dataset.get("tags") or [])[:200],
                "attributes": {
                    "organization": dataset.get("organization") or "",
                    "tags": dataset.get("tags") or [],
                    "resources": dataset.get("resources") or [],
                    "resource_count": int(dataset.get("resource_count") or 0),
                    "metadata_modified": dataset.get("metadata_modified") or "",
                },
                "last_seen_at": manifest.get("updated_at") or "",
            }
            record = normalize_record(raw, catalog["catalog_id"])
            if record["record_id"] not in seen_records:
                seen_records.add(record["record_id"])
                records.append(record)
                source_pages.append({"url": landing, "canonical_url": landing, "fetched": False})
    metrics = catalog_metrics(catalogs, records, source_pages)
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
