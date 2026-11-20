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


def record_page_derived_catalog_analysis():
    report_path = ROOT / "data" / "page-derived-catalogs" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("page-derived catalog report metrics must be an object")
    source_pages = report.get("source_index_pages")
    if isinstance(source_pages, bool) or not isinstance(source_pages, int) or source_pages < 0:
        raise ValueError("page-derived report source_index_pages must be a non-negative integer")
    source_sha = report.get("source_index_sha256")
    if (not isinstance(source_sha, str) or len(source_sha) != 64
            or any(char not in "0123456789abcdef" for char in source_sha.lower())):
        raise ValueError("page-derived report source_index_sha256 must be a SHA-256 digest")
    timestamp = report.get("generated_at")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("page-derived report generated_at timestamp is required")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("page-derived report generated_at must be ISO 8601") from exc

    count_keys = (
        "pages_analyzed", "eligible_pages", "unique_catalogs_discovered",
        "duplicate_catalog_candidates", "subcatalogs_discovered", "unique_records",
        "media_references", "unique_media_urls", "records_staged",
        "source_terms_reviewed_records", "commercially_eligible_records",
        "records_with_provenance", "records_with_timestamps",
        "cross_host_record_merges", "catalogs_with_records",
        "catalogs_without_records", "unassigned_records",
    )
    for key in count_keys:
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"page-derived report metric {key} must be a non-negative integer")
    for key in (
        "coverage_percentage", "unique_source_coverage_percentage",
        "eligible_page_coverage_percentage", "eligible_unique_source_page_coverage_percentage",
    ):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"page-derived report metric {key} must be a percentage from 0 to 100")
    by_type = metrics.get("unique_records_by_type")
    if not isinstance(by_type, dict) or any(
        not isinstance(kind, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0
        for kind, count in by_type.items()
    ):
        raise ValueError("page-derived unique_records_by_type must contain non-negative integer counts")

    throughput = report.get("throughput")
    if throughput is not None:
        if not isinstance(throughput, dict):
            raise ValueError("page-derived throughput must be an object or null")
        for key in ("pages_per_minute", "catalogs_per_minute", "records_per_minute"):
            value = throughput.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"page-derived throughput {key} must be a non-negative rate")
        if throughput.get("measurement") != "persisted_count_delta_over_wall_clock_interval":
            raise ValueError("page-derived throughput must use persisted count deltas over wall-clock time")

    previous_counts = report.get("previous_counts")
    count_deltas = report.get("count_deltas")
    if previous_counts is not None and not isinstance(previous_counts, dict):
        raise ValueError("page-derived previous_counts must be an object or null")
    if count_deltas is not None and not isinstance(count_deltas, dict):
        raise ValueError("page-derived count_deltas must be an object or null")
    elapsed = report.get("elapsed_wall_clock_seconds")
    if elapsed is not None and (
        isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed <= 0
    ):
        raise ValueError("page-derived elapsed_wall_clock_seconds must be positive or null")

    run_id = os.environ.get("GITHUB_RUN_ID", "manual")
    metric_keys = count_keys + (
        "unique_records_by_type", "coverage_percentage", "unique_source_coverage_percentage",
        "eligible_page_coverage_percentage", "eligible_unique_source_page_coverage_percentage",
    )
    event_metrics = {key: metrics[key] for key in metric_keys}
    event_metrics.update({
        "source_index_pages": source_pages,
        "records_published": report.get("records_published", metrics.get("records_published", 0)),
        "catalogs_verified": report.get("catalogs_verified", metrics.get("catalogs_verified", 0)),
        "previous_counts": previous_counts,
        "count_deltas": count_deltas,
        "elapsed_wall_clock_seconds": elapsed,
        "analysis_elapsed_seconds": report.get("analysis_elapsed_seconds"),
        "throughput": throughput,
    })
    event = {
        "id": "page-derived-catalog-analysis-" + run_id,
        "timestamp": timestamp,
        "type": "page_derived_catalog_analysis",
        "status": "validated_staging",
        "summary": (
            "Page-derived catalog registry and per-catalog indexes passed integrity validation; "
            "candidate records remain staging-only pending source-terms review and production indexing."
        ),
        "metrics": event_metrics,
        "source": {
            "workflow_run_id": run_id,
            "repository": "rshure001/EverythingHub-Crawler",
            "source_index_sha256": source_sha,
        },
    }
    return append_event(event, LEDGER)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-catalog-discovery", action="store_true")
    parser.add_argument("--record-page-derived-catalog-analysis", action="store_true")
    args = parser.parse_args()
    if args.record_catalog_discovery:
        print("Ledger event recorded." if record_catalog() else "Ledger event already exists; no duplicate added.")
    elif args.record_page_derived_catalog_analysis:
        print("Ledger event recorded." if record_page_derived_catalog_analysis() else "Ledger event already exists; no duplicate added.")
    else:
        ledger = load_ledger()
        print(json.dumps({"events": len(ledger["events"]), "latest": ledger["events"][-1] if ledger["events"] else None}, indent=2))

if __name__ == "__main__":
    main()
