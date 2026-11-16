#!/usr/bin/env python3
"""Decide whether the hourly crawler needs one safe recovery dispatch."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ATTEMPT_AGE_SECONDS = 75 * 60
ACTIVE_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate(runs: list[dict[str, Any]], now: datetime | None = None,
             max_age_seconds: int = MAX_ATTEMPT_AGE_SECONDS) -> dict[str, Any]:
    """Return wait, healthy, or dispatch; never retry a recent failed attempt."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    active = [run for run in runs if run.get("status") in ACTIVE_STATUSES]
    if active:
        return {
            "action": "wait",
            "reason": "a crawler run is already queued or running",
            "active_runs": len(active),
        }

    attempts = [
        run for run in runs
        if isinstance(run.get("created_at"), str)
    ]
    if not attempts:
        return {
            "action": "dispatch",
            "reason": "no crawler attempt is present in the recent run history",
            "age_seconds": None,
        }

    latest = max(attempts, key=lambda run: parse_time(run["created_at"]))
    age = max(0, int((now - parse_time(latest["created_at"])).total_seconds()))
    if age <= max_age_seconds:
        if latest.get("conclusion") == "success":
            return {
                "action": "healthy",
                "reason": "the latest crawler attempt completed successfully within the recovery window",
                "age_seconds": age,
                "last_attempt": latest["created_at"],
            }
        return {
            "action": "wait",
            "reason": "the latest crawler attempt did not succeed; wait for the retry window before dispatching again",
            "age_seconds": age,
            "last_attempt": latest["created_at"],
            "last_conclusion": latest.get("conclusion"),
        }

    return {
        "action": "dispatch",
        "reason": "the latest crawler attempt is older than the recovery window",
        "age_seconds": age,
        "last_attempt": latest["created_at"],
        "last_conclusion": latest.get("conclusion"),
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
