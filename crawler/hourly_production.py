#!/usr/bin/env python3
"""Persist per-run crawl production and calculate a true rolling hourly total."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "data" / "index.json"
HISTORY_PATH = ROOT / "data" / "hourly-production.json"
RETENTION = timedelta(hours=24)
WINDOW = timedelta(hours=1)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_snapshot(index: dict, history: dict | None = None, *, now=None,
                   run_id="local", run_number="local", commit_sha="local") -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    history = history if isinstance(history, dict) else {}
    runs = history.get("runs", [])
    runs = [row for row in runs if isinstance(row, dict) and row.get("run_id") != str(run_id)]

    records = max(0, int(index.get("pages_indexed_this_run", 0) or 0))
    duration = max(0.0, float(index.get("run_duration_seconds", 0) or 0))
    attempts = max(0, int(index.get("fetch_attempts_this_run", index.get("pages_crawled_this_run", 0)) or 0))
    runs.append({
        "run_id": str(run_id),
        "run_number": str(run_number),
        "timestamp": now.isoformat(),
        "indexed_records": records,
        "fetch_attempts": attempts,
        "duration_seconds": duration,
        "commit_sha": str(commit_sha)[:12],
    })

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
    recent = [row for row in retained if parse_time(row["timestamp"]) >= window_start]
    indexed_total = sum(max(0, int(row.get("indexed_records", 0) or 0)) for row in recent)
    attempts_total = sum(max(0, int(row.get("fetch_attempts", 0) or 0)) for row in recent)
    if recent:
        first = recent[0]
        elapsed = max(0.0, (now - parse_time(first["timestamp"])).total_seconds())
        coverage = min(3600.0, max(1.0, elapsed + max(0.0, float(first.get("duration_seconds", 0) or 0))))
    else:
        coverage = 0.0
    full_window = coverage >= 3599
    hourly_pace = indexed_total / coverage * 3600 if coverage else 0.0

    return {
        "updated_at": now.isoformat(),
        "target_new_records_per_hour": 1000,
        "runs": retained,
        "rolling_60m": {
            "indexed_records": indexed_total,
            "fetch_attempts": attempts_total,
            "run_count": len(recent),
            "coverage_seconds": round(coverage, 1),
            "full_60_minute_window": full_window,
            "hourly_pace_equivalent": round(hourly_pace, 1),
            "target_met": indexed_total >= 1000 if full_window else hourly_pace >= 1000,
        },
    }


def main():
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else {}
    snapshot = build_snapshot(
        index, history,
        run_id=os.environ.get("GITHUB_RUN_ID", "local"),
        run_number=os.environ.get("GITHUB_RUN_NUMBER", "local"),
        commit_sha=os.environ.get("GITHUB_SHA", "local"),
    )
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    metrics = snapshot["rolling_60m"]
    coverage_minutes = metrics["coverage_seconds"] / 60
    label = "full rolling 60-minute window" if metrics["full_60_minute_window"] else f"partial window ({coverage_minutes:.1f} minutes of evidence)"
    status = "TARGET MET" if metrics["target_met"] else "BELOW TARGET"
    report = (
        "## Everything Hub — rolling crawl production\n\n"
        f"- New indexed records in the last 60 minutes: **{metrics['indexed_records']:,}**\n"
        f"- Evidence window: **{label}**\n"
        f"- Hourly pace equivalent: **{metrics['hourly_pace_equivalent']:,.0f} new records/hour**\n"
        f"- Fetch attempts in the same window (not counted as new records): **{metrics['fetch_attempts']:,}**\n"
        f"- Completed crawl runs represented: **{metrics['run_count']}**\n"
        f"- Target: **1,000 new indexed records/hour — {status}**\n"
        "- A partial window is explicitly labeled; it is not presented as a completed 60-minute measurement.\n"
    )
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)


if __name__ == "__main__":
    main()
