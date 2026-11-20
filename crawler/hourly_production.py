#!/usr/bin/env python3
"""Measure unique catalog growth from canonical manifest deltas."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "data" / "index.json"
MANIFEST_PATH = ROOT / "public-search" / "manifest.json"
HISTORY_PATH = ROOT / "data" / "hourly-production.json"
RETENTION = timedelta(hours=24)
WINDOW = timedelta(hours=1)
TARGET_RECORDS_PER_HOUR = 60000


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_manifest_snapshot(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("manifest snapshot must be an object")
    pages = value.get("total_pages")
    updated_at = value.get("updated_at")
    if type(pages) is not int or pages < 0:
        raise ValueError("manifest total_pages must be a non-negative integer")
    if type(updated_at) is not int or updated_at <= 0:
        raise ValueError("manifest updated_at must be a positive Unix timestamp")
    stamp = datetime.fromtimestamp(updated_at, timezone.utc)
    return {
        "total_pages": pages,
        "updated_at": updated_at,
        "updated_at_iso": stamp.isoformat().replace("+00:00", "Z"),
    }


def measure_manifest_delta(before: dict | None, after: dict | None) -> dict:
    """Return a measured net unique-page delta only for a valid timestamped pair."""
    result = {
        "valid": False,
        "reason": "manifest_baseline_unavailable",
        "before_pages": None,
        "after_pages": None,
        "before_updated_at": None,
        "after_updated_at": None,
        "elapsed_seconds": None,
        "new_unique_records": None,
        "records_per_minute": None,
        "records_per_hour": None,
    }
    if before is None or after is None:
        return result

    try:
        first = validate_manifest_snapshot(before)
        last = validate_manifest_snapshot(after)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        result["reason"] = f"invalid_manifest_snapshot: {exc}"
        return result

    elapsed = float(last["updated_at"] - first["updated_at"])
    delta = last["total_pages"] - first["total_pages"]
    result.update({
        "before_pages": first["total_pages"],
        "after_pages": last["total_pages"],
        "before_updated_at": first["updated_at_iso"],
        "after_updated_at": last["updated_at_iso"],
        "elapsed_seconds": elapsed,
    })
    if elapsed <= 0:
        result["reason"] = "manifest_timestamps_not_increasing"
        return result

    result.update({
        "valid": True,
        "reason": None,
        "new_unique_records": delta,
        "records_per_minute": round(delta * 60 / elapsed, 2),
        "records_per_hour": round(delta * 3600 / elapsed, 1),
    })
    return result


def build_snapshot(index: dict, history: dict | None = None, *, now=None,
                   run_id="local", run_number="local", commit_sha="local",
                   manifest_before=None, manifest_after=None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    history = history if isinstance(history, dict) else {}
    runs = history.get("runs", [])
    runs = [
        row for row in runs
        if isinstance(row, dict) and row.get("run_id") != str(run_id)
    ]

    records_processed = max(0, int(index.get("pages_indexed_this_run", 0) or 0))
    duration = max(0.0, float(index.get("run_duration_seconds", 0) or 0))
    attempts = max(
        0,
        int(index.get(
            "fetch_attempts_this_run",
            index.get("pages_crawled_this_run", 0),
        ) or 0),
    )
    interval = measure_manifest_delta(manifest_before, manifest_after)
    timestamp = interval["after_updated_at"] if interval["valid"] else now.isoformat()

    run = {
        "run_id": str(run_id),
        "run_number": str(run_number),
        "timestamp": timestamp,
        "indexed_records_processed": records_processed,
        "fetch_attempts": attempts,
        "duration_seconds": duration,
        "commit_sha": str(commit_sha)[:12],
        "manifest_interval": interval,
    }
    runs.append(run)

    retention_start = now - RETENTION
    retained = []
    for row in runs:
        try:
            stamp = parse_time(row["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if retention_start <= stamp <= now:
            retained.append(row)
    retained.sort(key=lambda row: parse_time(row["timestamp"]))

    window_start = now - WINDOW
    recent = [
        row for row in retained
        if parse_time(row["timestamp"]) >= window_start
    ]
    recent_activity = {
        "records_processed": sum(
            max(0, int(row.get("indexed_records_processed", row.get("indexed_records", 0)) or 0))
            for row in recent
        ),
        "fetch_attempts": sum(
            max(0, int(row.get("fetch_attempts", 0) or 0))
            for row in recent
        ),
        "run_count": len(recent),
    }

    return {
        "updated_at": now.isoformat(),
        "target_new_records_per_hour": TARGET_RECORDS_PER_HOUR,
        "runs": retained,
        "latest_manifest_interval": interval,
        "recent_run_activity": recent_activity,
    }


def main():
    index = load_index(INDEX_PATH)
    history = (
        json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if HISTORY_PATH.exists() else {}
    )

    before_pages = os.environ.get("EH_MANIFEST_BEFORE_PAGES")
    before_updated_at = os.environ.get("EH_MANIFEST_BEFORE_UPDATED_AT")
    if before_pages is None or before_updated_at is None:
        raise SystemExit(
            "Canonical manifest baseline is missing; refusing to label per-run "
            "upserts as new unique records."
        )

    manifest_before = {
        "total_pages": int(before_pages),
        "updated_at": int(before_updated_at),
    }
    manifest_after = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    snapshot = build_snapshot(
        index,
        history,
        run_id=os.environ.get("GITHUB_RUN_ID", "local"),
        run_number=os.environ.get("GITHUB_RUN_NUMBER", "local"),
        commit_sha=os.environ.get("GITHUB_SHA", "local"),
        manifest_before=manifest_before,
        manifest_after=manifest_after,
    )
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(snapshot, indent=2) + "\n",
        encoding="utf-8",
    )

    interval = snapshot["latest_manifest_interval"]
    activity = snapshot["recent_run_activity"]
    report = ["## Everything Hub — canonical manifest growth"]
    if interval["valid"]:
        delta = interval["new_unique_records"]
        report.extend([
            "",
            f"- Manifest before: **{interval['before_pages']:,} pages** at {interval['before_updated_at']}",
            f"- Manifest after: **{interval['after_pages']:,} pages** at {interval['after_updated_at']}",
            f"- Net new unique pages by manifest count: **{delta:+,}**",
            f"- Elapsed wall-clock: **{interval['elapsed_seconds']:.0f} seconds**",
            (
                f"- Measured rate: **{interval['records_per_minute']:.2f} records/minute**; "
                f"**{interval['records_per_hour']:.1f} records/hour** normalized from this interval"
            ),
            f"- Total indexed pages: **{interval['after_pages']:,}**",
        ])
    else:
        report.extend([
            "",
            f"- Unique throughput: **not reported** ({interval['reason']})",
            "- Per-run upserts are not substituted for unique manifest growth.",
        ])

    report.extend([
        f"- Records processed/upserted this run (not unique additions): **{index.get('pages_indexed_this_run', 0):,}**",
        f"- Fetch attempts this run: **{index.get('fetch_attempts_this_run', index.get('pages_crawled_this_run', 0)):,}**",
        f"- Fetch/parse failures: **{index.get('fetch_or_parse_errors_this_run', 0):,}**",
        f"- Robots-blocked URLs: **{index.get('robots_blocked_this_run', 0):,}**",
        f"- Robots-policy failures (fail-closed): **{index.get('robots_policy_failures_this_run', 0):,}**",
        f"- Rate-limited responses: **{index.get('rate_limited_responses_this_run', 0):,}**",
        f"- Transient retries queued: **{index.get('transient_retries_queued_this_run', 0):,}**",
        f"- Frontier backlog: **{index.get('queue_remaining', 0):,}**",
        (
            f"- Recent crawl activity (last hour by run timestamp): "
            f"{activity['records_processed']:,} processed/upserted records across "
            f"{activity['run_count']} runs; {activity['fetch_attempts']:,} fetch attempts. "
            "This is not a unique-record count."
        ),
    ])
    text = "\n".join(report) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)


if __name__ == "__main__":
    main()
