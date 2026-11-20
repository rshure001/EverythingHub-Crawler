#!/usr/bin/env python3
"""Append verified, idempotent events to the Everything Hub project ledger."""
from __future__ import annotations
import argparse, gzip, json, os, pathlib, tempfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "project-ledger.json"

def load_ledger(path=LEDGER):
    if not path.exists():
        return {"schema_version": 1, "project": "Everything Hub", "purpose": "Append-only verified project history.", "events": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("events"), list):
        raise ValueError("unsupported or malformed project ledger")
    ids = [event.get("id") for event in value["events"]]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("ledger event IDs must be unique non-empty strings")
    return value

def append_event(event, path=LEDGER):
    if not isinstance(event, dict) or not all(event.get(key) for key in ("id", "timestamp", "type", "status", "summary")):
        raise ValueError("event requires id, timestamp, type, status, and summary")
    ledger = load_ledger(path)
    if any(item["id"] == event["id"] for item in ledger["events"]):
        return False
    ledger["events"].append(event)
    ledger["events"].sort(key=lambda item: (item["timestamp"], item["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(ledger, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True

def _read_json_or_gzip(path):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return json.loads(raw.decode("utf-8"))

def record_catalog():
    data = ROOT / "data"
    manifest = _read_json_or_gzip(data / "catalogs.json.gz")
    openlibrary_path = data / "openlibrary-catalogs.json.gz"
    openlibrary = _read_json_or_gzip(openlibrary_path) if openlibrary_path.exists() else {
        "catalog_count": 0, "datasets_discovered": 0, "errors": []
    }
    universal_path = data / "universal-catalog-records.json.gz"
    universal = _read_json_or_gzip(universal_path) if universal_path.exists() else {"metrics": {}}
    run_id = os.environ.get("GITHUB_RUN_ID", "manual")
    metrics = universal.get("metrics", {})
    event = {
        "id": "catalog-discovery-" + run_id,
        "timestamp": manifest.get("updated_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "type": "catalog_discovery",
        "status": "success",
        "summary": "Catalog-first discovery completed across CKAN and Open Library subject collections.",
        "metrics": {
            "catalogs": manifest["catalog_count"] + openlibrary["catalog_count"],
            "catalog_records": metrics.get("catalog_records", manifest["datasets_discovered"] + openlibrary["datasets_discovered"]),
            "unique_entities": metrics.get("unique_entities", 0),
            "ckan_datasets": manifest["datasets_discovered"],
            "ckan_resources": manifest["resources_discovered"],
            "openlibrary_records": openlibrary["datasets_discovered"],
            "source_page_references": metrics.get("source_page_references", 0),
            "source_pages_fetched": metrics.get("source_pages_fetched", 0),
            "indexed_records": metrics.get("indexed_records", 0),
        },
        "source": {"workflow_run_id": run_id, "repository": "rshure001/EverythingHub-Crawler"}
    }
    return append_event(event, LEDGER)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-catalog-discovery", action="store_true")
    args = parser.parse_args()
    if args.record_catalog_discovery:
        print("Ledger event recorded." if record_catalog() else "Ledger event already exists; no duplicate added.")
    else:
        ledger = load_ledger()
        print(json.dumps({"events": len(ledger["events"]), "latest": ledger["events"][-1] if ledger["events"] else None}, indent=2))

if __name__ == "__main__":
    main()
