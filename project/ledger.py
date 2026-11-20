#!/usr/bin/env python3
"""Append verified, idempotent events to the Everything Hub project ledger."""
from __future__ import annotations
import argparse, json, os, pathlib, tempfile
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

def record_catalog():
    manifest = json.loads((ROOT / "data" / "catalogs.json").read_text(encoding="utf-8"))
    run_id = os.environ.get("GITHUB_RUN_ID", "manual")
    event = {
        "id": "catalog-discovery-" + run_id,
        "timestamp": manifest.get("updated_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "type": "catalog_discovery",
        "status": "success",
        "summary": "Catalog-first public data discovery completed.",
        "metrics": {"catalogs": manifest["catalog_count"], "datasets": manifest["datasets_discovered"], "resources": manifest["resources_discovered"]},
        "source": {"workflow_run_id": run_id, "repository": "rshure001/EverythingHub-Crawler"}
    }
    return append_event(event)

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
