#!/usr/bin/env python3
"""Decide whether to update the crawler recovery marker after a missed schedule."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_MANIFEST_AGE_SECONDS = 10 * 60
MAX_MANIFEST_FUTURE_SKEW_SECONDS = 60
MAX_SCHEDULE_AGE_SECONDS = 10 * 60
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


def schedule_health(
    runs: list[dict[str, Any]],
    now: datetime,
    max_schedule_age_seconds: int = MAX_SCHEDULE_AGE_SECONDS,
) -> dict[str, Any]:
    """Measure scheduled delivery independently from push/manual crawler runs."""
    scheduled = []
    for run in runs:
        if not isinstance(run, dict) or run.get("event") != "schedule":
            continue
        created_at = run.get("created_at")
        if not isinstance(created_at, str):
            continue
        try:
            scheduled.append((parse_time(created_at), run))
        except ValueError:
            continue

    if not scheduled:
        return {
            "schedule_status": "unobserved",
            "schedule_age_seconds": None,
            "last_scheduled_run": None,
            "last_scheduled_conclusion": None,
            "last_scheduled_workflow_conclusion": None,
        }

    created_at, latest = max(scheduled, key=lambda item: item[0])
    age = max(0, int((now - created_at).total_seconds()))
    crawl_conclusion = (
        latest.get("crawl_execution_conclusion")
        if "crawl_execution_conclusion" in latest
        else latest.get("conclusion")
    )
    return {
        "schedule_status": "recent" if age <= max_schedule_age_seconds else "stale",
        "schedule_age_seconds": age,
        "last_scheduled_run": latest.get("created_at"),
        "last_scheduled_conclusion": crawl_conclusion,
        "last_scheduled_workflow_conclusion": latest.get("conclusion"),
    }


def evaluate(
    runs: list[dict[str, Any]],
    now: datetime | None = None,
    manifest_updated_at: Any | None = None,
    max_manifest_age_seconds: int = MAX_MANIFEST_AGE_SECONDS,
    max_schedule_age_seconds: int = MAX_SCHEDULE_AGE_SECONDS,
) -> dict[str, Any]:
    """Return wait, healthy, or mark using the three explicit recovery gates."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    runs = runs if isinstance(runs, list) else []
    schedule = schedule_health(runs, now, max_schedule_age_seconds)

    manifest_time = parse_manifest_time(manifest_updated_at)
    if manifest_updated_at is not None and manifest_time is None:
        raise ValueError("public search manifest has an invalid updated_at timestamp")

    manifest_age = None
    if manifest_time is not None:
        raw_manifest_age = (now - manifest_time).total_seconds()
        if raw_manifest_age < -MAX_MANIFEST_FUTURE_SKEW_SECONDS:
            raise ValueError("public search manifest updated_at timestamp is too far in the future")
        manifest_age = max(0, int(raw_manifest_age))

    active = [
        run for run in runs
        if isinstance(run, dict) and run.get("status") in ACTIVE_STATUSES
    ]
    attempts = []
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("created_at"), str):
            continue
        try:
            attempts.append((parse_time(run["created_at"]), run))
        except ValueError:
            continue
    latest_attempt = max(attempts, key=lambda item: item[0]) if attempts else None
    attempt_age = (
        max(0, int((now - latest_attempt[0]).total_seconds()))
        if latest_attempt else None
    )
    last_attempt = latest_attempt[1].get("created_at") if latest_attempt else None

    common = {
        "age_seconds": attempt_age,
        "last_attempt": last_attempt,
        "manifest_age_seconds": manifest_age,
        **schedule,
    }

    if active:
        return {
            "action": "wait",
            "reason": "a crawler run is already queued or running; do not update the recovery marker",
            "active_runs": len(active),
            **common,
        }

    if manifest_age is None:
        return {
            "action": "wait",
            "reason": "the public manifest timestamp is unavailable; do not infer that it is stale",
            **common,
        }

    if manifest_age <= max_manifest_age_seconds:
        return {
            "action": "healthy",
            "reason": "the public manifest is within the 10-minute freshness window; no recovery marker is needed",
            **common,
        }

    if schedule["schedule_status"] == "unobserved":
        if manifest_age <= max_schedule_age_seconds:
            return {
                "action": "wait",
                "reason": "no scheduled crawl event is observable, but the public manifest is not older than the 10-minute recovery window",
                **common,
            }
        return {
            "action": "mark",
            "reason": "no scheduled crawl event is observable, the public manifest is more than 10 minutes old, and no crawler is queued or running",
            **common,
        }

    if schedule["schedule_status"] != "stale":
        return {
            "action": "wait",
            "reason": "the latest scheduled crawl is not more than 10 minutes old; do not update the recovery marker",
            **common,
        }

    return {
        "action": "mark",
        "reason": "scheduled crawl delivery is more than 10 minutes stale, the public manifest is more than 10 minutes stale, and no crawler is queued or running",
        **common,
    }


def recovery_dispatch_decision(runs: list[dict[str, Any]], recovery_sha: str) -> dict[str, str]:
    """Fail closed unless one recovery dispatch is safe and has not already started."""
    if not isinstance(runs, list):
        raise ValueError("crawler workflow response must contain a workflow_runs list")
    if (
        not isinstance(recovery_sha, str)
        or len(recovery_sha) != 40
        or any(char not in "0123456789abcdef" for char in recovery_sha.lower())
    ):
        raise ValueError("recovery commit SHA must be a 40-character hexadecimal SHA")

    active = [
        run for run in runs
        if isinstance(run, dict)
        and run.get("name") == "Everything Hub crawler"
        and run.get("status") in ACTIVE_STATUSES
    ]
    if active:
        return {
            "action": "wait",
            "reason": "a crawler is queued or running after the recovery marker commit",
        }

    already_started = any(
        isinstance(run, dict)
        and run.get("name") == "Everything Hub crawler"
        and run.get("head_sha") == recovery_sha
        and run.get("event") in {"push", "workflow_dispatch"}
        for run in runs
    )
    if already_started:
        return {
            "action": "already_started",
            "reason": "a crawler run already references the recovery marker commit",
        }

    return {
        "action": "dispatch",
        "reason": "no crawler is active and no run has started from the recovery marker commit",
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
