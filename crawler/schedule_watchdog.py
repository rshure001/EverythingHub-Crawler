#!/usr/bin/env python3
"""Decide whether the autonomous crawler needs one safe recovery dispatch."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ATTEMPT_AGE_SECONDS = 10 * 60
MAX_MANIFEST_AGE_SECONDS = MAX_ATTEMPT_AGE_SECONDS
ACTIVE_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_manifest_time(value: Any) -> datetime | None:
    """Parse the crawler manifest's Unix timestamp or an ISO-8601 timestamp."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str) and value.strip():
            return parse_time(value)
    except (OverflowError, OSError, ValueError):
        return None
    return None


def evaluate(runs: list[dict[str, Any]], now: datetime | None = None,
             max_age_seconds: int = MAX_ATTEMPT_AGE_SECONDS,
             manifest_updated_at: Any | None = None,
             max_manifest_age_seconds: int = MAX_MANIFEST_AGE_SECONDS) -> dict[str, Any]:
    """Return wait, healthy, or dispatch; require both a recent run and fresh index."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    manifest_time = parse_manifest_time(manifest_updated_at)
    if manifest_updated_at is not None and manifest_time is None:
        raise ValueError("public search manifest has an invalid updated_at timestamp")
    manifest_age = (
        max(0, int((now - manifest_time).total_seconds()))
        if manifest_time is not None else None
    )

    active = [run for run in runs if run.get("status") in ACTIVE_STATUSES]
    if active:
        return {
            "action": "wait",
            "reason": "a crawler run is already queued or running",
            "active_runs": len(active),
            "manifest_age_seconds": manifest_age,
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
            "manifest_age_seconds": manifest_age,
        }

    latest = max(attempts, key=lambda run: parse_time(run["created_at"]))
    age = max(0, int((now - parse_time(latest["created_at"])).total_seconds()))
    if age <= max_age_seconds:
        if latest.get("conclusion") == "success":
            if manifest_age is not None and manifest_age > max_manifest_age_seconds:
                return {
                    "action": "wait",
                    "reason": "a recent crawl succeeded but the public manifest is stale; wait for the recovery window before dispatching again",
                    "age_seconds": age,
                    "last_attempt": latest["created_at"],
                    "manifest_age_seconds": manifest_age,
                }
            return {
                "action": "healthy",
                "reason": "the latest crawler attempt succeeded and the public manifest is fresh",
                "age_seconds": age,
                "last_attempt": latest["created_at"],
                "manifest_age_seconds": manifest_age,
            }
        return {
            "action": "wait",
            "reason": "the latest crawler attempt did not succeed; wait for the retry window before dispatching again",
            "age_seconds": age,
            "last_attempt": latest["created_at"],
            "last_conclusion": latest.get("conclusion"),
            "manifest_age_seconds": manifest_age,
        }

    return {
        "action": "dispatch",
        "reason": "the latest crawler attempt is older than the recovery window",
        "age_seconds": age,
        "last_attempt": latest["created_at"],
        "last_conclusion": latest.get("conclusion"),
        "manifest_age_seconds": manifest_age,
    }


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: schedule_watchdog.py <workflow-runs.json> [public-manifest.json]")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise SystemExit("GitHub workflow response did not contain a workflow_runs array")
    manifest_updated_at = None
    if len(sys.argv) == 3:
        manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise SystemExit("public search manifest did not contain a JSON object")
        manifest_updated_at = manifest.get("updated_at")
        if parse_manifest_time(manifest_updated_at) is None:
            raise SystemExit("public search manifest is missing a valid updated_at timestamp")
    print(json.dumps(evaluate(runs, manifest_updated_at=manifest_updated_at), sort_keys=True))


if __name__ == "__main__":
    main()
