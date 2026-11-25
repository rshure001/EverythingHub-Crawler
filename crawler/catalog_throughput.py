#!/usr/bin/env python3
"""Measure catalog growth from successive persisted snapshots using wall-clock time."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

SCALAR_FIELDS = (
    "source_index_pages",
    "pages_analyzed",
    "eligible_pages",
    "unique_catalogs_discovered",
    "duplicate_catalog_candidates",
    "subcatalogs_discovered",
    "unique_records",
    "records_assigned_to_catalogs",
    "unassigned_records",
    "media_references",
    "unique_media_urls",
)
RATE_FIELDS = {
    "pages_analyzed": "pages_per_minute",
    "unique_catalogs_discovered": "catalogs_per_minute",
    "unique_records": "records_per_minute",
}


def _read_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _counts(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics") or {}
    counts = {
        name: report.get(name) if name == "source_index_pages" else metrics.get(name)
        for name in SCALAR_FIELDS
    }
    counts["unique_records_by_type"] = metrics.get("unique_records_by_type") or {}
    counts["catalogs_verified"] = report.get("catalogs_verified", 0)
    counts["records_published"] = report.get("records_published", 0)
    return counts


def capture_baseline(report_path: Path, output_path: Path) -> dict[str, Any]:
    baseline: dict[str, Any] = {"available": False, "reason": "no prior persisted catalog report"}
    if report_path.exists():
        report = _read_report(report_path)
        persisted_at = report.get("persisted_snapshot_at") or report.get("generated_at")
        counts = _counts(report)
        required = ("source_index_pages", "pages_analyzed", "unique_catalogs_discovered", "unique_records")
        if _timestamp(persisted_at) and all(isinstance(counts.get(key), int) for key in required):
            baseline = {"available": True, "persisted_snapshot_at": persisted_at, "counts": counts}
        else:
            baseline = {"available": False, "reason": "prior report lacks a valid persisted timestamp or cumulative counts"}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return baseline


def record_throughput(report_path: Path, baseline_path: Path) -> dict[str, Any]:
    report = _read_report(report_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {"available": False}
    current_at = report.get("persisted_snapshot_at") or report.get("analysis_finished_at") or report.get("generated_at")
    current_time = _timestamp(current_at)
    current_counts = _counts(report)

    if not baseline.get("available") or not current_time:
        report["previous_counts"] = None
        report["count_deltas"] = None
        report["negative_count_deltas"] = []
        report["elapsed_wall_clock_seconds"] = None
        report["throughput"] = None
        report["throughput_status"] = "baseline_unavailable"
        report["throughput_note"] = baseline.get("reason", "no prior persisted snapshot")
    else:
        previous_time = _timestamp(baseline.get("persisted_snapshot_at"))
        previous_counts = baseline.get("counts") or {}
        elapsed = (current_time - previous_time).total_seconds() if previous_time else 0.0
        report["previous_counts"] = {"persisted_snapshot_at": baseline.get("persisted_snapshot_at"), **previous_counts}
        deltas: dict[str, Any] = {}
        negative: list[str] = []
        for field in SCALAR_FIELDS:
            before, after = previous_counts.get(field), current_counts.get(field)
            if isinstance(before, int) and isinstance(after, int):
                deltas[field] = after - before
                if after - before < 0:
                    negative.append(field)
        before_types = previous_counts.get("unique_records_by_type") or {}
        after_types = current_counts.get("unique_records_by_type") or {}
        type_deltas = {
            key: after_types.get(key, 0) - before_types.get(key, 0)
            for key in sorted(set(before_types) | set(after_types))
        }
        deltas["unique_records_by_type"] = type_deltas
        negative.extend(f"unique_records_by_type.{key}" for key, value in type_deltas.items() if value < 0)
        for field in ("catalogs_verified", "records_published"):
            deltas[field] = current_counts.get(field, 0) - previous_counts.get(field, 0)
            if deltas[field] < 0:
                negative.append(field)
        report["count_deltas"] = deltas
        report["negative_count_deltas"] = negative
        report["elapsed_wall_clock_seconds"] = round(elapsed, 3) if elapsed > 0 else None
        if elapsed > 0:
            minutes = elapsed / 60.0
            report["throughput"] = {
                "basis": "persisted cumulative count deltas divided by elapsed wall-clock minutes",
                "pages_per_minute": round(deltas.get("pages_analyzed", 0) / minutes, 4),
                "catalogs_per_minute": round(deltas.get("unique_catalogs_discovered", 0) / minutes, 4),
                "records_per_minute": round(deltas.get("unique_records", 0) / minutes, 4),
            }
            report["throughput_status"] = "measured"
            report.pop("throughput_note", None)
        else:
            report["throughput"] = None
            report["throughput_status"] = "invalid_elapsed_time"
            report["throughput_note"] = "The new persisted timestamp is not later than the previous snapshot."
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture", help="capture the previous persisted snapshot counts")
    capture.add_argument("--report", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    record = subparsers.add_parser("record", help="write count deltas and separate wall-clock rates")
    record.add_argument("--report", type=Path, required=True)
    record.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        result = capture_baseline(args.report, args.output)
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        result = record_throughput(args.report, args.baseline)
        print(json.dumps({
            "throughput_status": result.get("throughput_status"),
            "elapsed_wall_clock_seconds": result.get("elapsed_wall_clock_seconds"),
            "count_deltas": result.get("count_deltas"),
            "throughput": result.get("throughput"),
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
