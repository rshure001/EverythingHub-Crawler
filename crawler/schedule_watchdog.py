#!/usr/bin/env python3
"""Decide whether the hourly crawler needs one safe recovery dispatch."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_SUCCESS_AGE_SECONDS = 75 * 60
ACTIVE_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate(runs: list[dict[str, Any]], now: datetime | None = None,
             max_age_seconds: int = MAX_SUCCESS_AGE_SECONDS) -> dict[str, Any]:
    """Return wait, healthy, or dispatch; never dispatch while a run is active."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    active = [run for run in runs if run.get("status") in ACTIVE_STATUSES]
    if active:
        return {
            "action": "wait",
            "reason": "a crawler run is already queued or running",
            "active_runs": len(active),
        }

    successful = [
        run for run in runs
        if run.get("conclusion") == "success" and isinstance(run.get("created_at"), str)
    ]
    if not successful:
        return {
            "action": "dispatch",
            "reason": "no successful crawler run is present in the recent run history",
            "age_seconds": None,
        }

    latest = max(successful, key=lambda run: parse_time(run["created_at"]))
    age = max(0, int((now - parse_time(latest["created_at"])).total_seconds()))
    if age > max_age_seconds:
        return {
            "action": "dispatch",
            "reason": "last successful crawler run is older than the 75-minute recovery window",
            "age_seconds": age,
            "last_success": latest["created_at"],
        }
    return {
        "action": "healthy",
        "reason": "a successful crawler run completed within the recovery window",
        "age_seconds": age,
        "last_success": latest["created_at"],
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: schedule_watchdog.py <workflow-runs.json>")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise SystemExit("GitHub workflow response did not contain a workflow_runs array")
    print(json.dumps(evaluate(runs), sort_keys=True))


if __name__ == "__main__":
    main()
