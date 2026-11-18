#!/usr/bin/env python3
"""Report publisher and live-search smoke health without triggering retries."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}
MAX_RELEASE_AGE_SECONDS = 30 * 60


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_workflow(
    payload: dict[str, Any],
    workflow: str,
    now: datetime | None = None,
    max_age_seconds: int = MAX_RELEASE_AGE_SECONDS,
) -> dict[str, Any]:
    """Classify the latest run; missing or malformed history never counts as healthy."""
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise ValueError(f"{workflow} response must contain a workflow_runs array")

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for run in payload["workflow_runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("created_at"), str):
            continue
        try:
            candidates.append((parse_time(run["created_at"]), run))
        except ValueError:
            continue

    if not candidates:
        return {
            "workflow": workflow,
            "status": "unobserved",
            "run_id": None,
            "run_number": None,
            "created_at": None,
            "conclusion": None,
            "age_seconds": None,
        }

    created_at, latest = max(candidates, key=lambda item: item[0])
    age = max(0, int((now - created_at).total_seconds()))
    run_status = latest.get("status")
    conclusion = latest.get("conclusion")

    if run_status in ACTIVE_STATUSES:
        status = "running"
    elif run_status == "completed" and conclusion == "success":
        status = "healthy" if age <= max_age_seconds else "stale"
    elif run_status == "completed":
        status = "failed"
    else:
        status = "unobserved"

    return {
        "workflow": workflow,
        "status": status,
        "run_id": latest.get("id"),
        "run_number": latest.get("run_number"),
        "created_at": latest.get("created_at"),
        "conclusion": conclusion,
        "age_seconds": age,
    }


def evaluate_pipeline(
    publisher_payload: dict[str, Any],
    search_payload: dict[str, Any],
    now: datetime | None = None,
    max_age_seconds: int = MAX_RELEASE_AGE_SECONDS,
) -> dict[str, Any]:
    """Report downstream release checks separately from crawler freshness."""
    publisher = evaluate_workflow(publisher_payload, "publisher", now, max_age_seconds)
    search = evaluate_workflow(search_payload, "search_smoke", now, max_age_seconds)
    states = {publisher["status"], search["status"]}
    if "failed" in states:
        overall = "failed"
    elif "running" in states:
        overall = "running"
    elif "stale" in states:
        overall = "stale"
    elif "unobserved" in states:
        overall = "unobserved"
    else:
        overall = "healthy"
    return {"overall_status": overall, "publisher": publisher, "search_smoke": search}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: pipeline_health.py <publisher-runs.json> <search-smoke-runs.json>")
    publisher = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    search = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(evaluate_pipeline(publisher, search), sort_keys=True))


if __name__ == "__main__":
    main()
