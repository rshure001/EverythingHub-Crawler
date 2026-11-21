#!/usr/bin/env python3
"""Report publisher, Pages deploy-job, and live-search smoke health without retries."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}
MAX_RELEASE_AGE_SECONDS = 30 * 60
MAX_SEARCH_MANIFEST_AGE_SECONDS = 15 * 60
MAX_MANIFEST_FUTURE_SKEW_SECONDS = 60


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)



def evaluate_manifest_freshness(
    manifest: dict[str, Any],
    now: datetime | None = None,
    max_age_seconds: int = MAX_SEARCH_MANIFEST_AGE_SECONDS,
) -> dict[str, Any]:
    """Classify the committed search manifest by age; invalid timestamps fail closed."""
    if not isinstance(manifest, dict):
        raise ValueError("public search manifest must be a JSON object")
    updated_at = manifest.get("updated_at")
    if isinstance(updated_at, bool) or not isinstance(updated_at, int) or updated_at <= 0:
        raise ValueError("public search manifest has an invalid updated_at timestamp")

    try:
        manifest_time = datetime.fromtimestamp(updated_at, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("public search manifest has an invalid updated_at timestamp") from exc

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw_age = (now - manifest_time).total_seconds()
    if raw_age < -MAX_MANIFEST_FUTURE_SKEW_SECONDS:
        raise ValueError("public search manifest updated_at timestamp is too far in the future")
    age = max(0, int(raw_age))
    return {
        "status": "fresh" if age <= max_age_seconds else "stale",
        "age_seconds": age,
        "max_age_seconds": max_age_seconds,
        "updated_at": updated_at,
    }


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


def evaluate_deploy_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Report the Pages deploy job separately; success is not live-domain verification."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("publisher jobs response must contain a jobs array")
    deploy_jobs = [
        job for job in payload["jobs"]
        if isinstance(job, dict) and str(job.get("name", "")).strip().lower() in {"deploy", "deployment"}
    ]
    if not deploy_jobs:
        return {"status": "unobserved", "conclusion": None, "job_name": None}

    latest = deploy_jobs[-1]
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    if status in ACTIVE_STATUSES:
        result = "running"
    elif status == "completed" and conclusion == "success":
        result = "job_succeeded_unverified"
    elif status == "completed" and conclusion == "skipped":
        result = "skipped"
    elif status == "completed":
        result = "failed"
    else:
        result = "unobserved"
    return {
        "status": result,
        "conclusion": conclusion,
        "job_name": latest.get("name"),
    }


def evaluate_pipeline(
    publisher_payload: dict[str, Any],
    publisher_jobs_payload: dict[str, Any],
    search_payload: dict[str, Any],
    now: datetime | None = None,
    max_age_seconds: int = MAX_RELEASE_AGE_SECONDS,
) -> dict[str, Any]:
    """Report artifact build, deploy-job state, and live-search smoke independently."""
    # The legacy GitHub Pages publisher is intentionally not part of the
    # Everything Hub crawler path. Keep its historical fields explicit but
    # exclude them from pipeline health so an unused publisher cannot mask
    # the actual live-search verification result.
    publisher = {
        "workflow": "publisher",
        "status": "not_used",
        "run_id": None,
        "run_number": None,
        "created_at": None,
        "conclusion": None,
        "age_seconds": None,
    }
    deploy_job = {"status": "not_used", "conclusion": None, "job_name": None}
    search = evaluate_workflow(search_payload, "search_smoke", now, max_age_seconds)
    return {
        "overall_status": search["status"],
        "publisher": publisher,
        "deploy_job": deploy_job,
        "search_smoke": search,
    }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: pipeline_health.py <publisher-runs.json> <publisher-jobs.json> <search-smoke-runs.json>"
        )
    publisher = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    publisher_jobs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    search = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    print(json.dumps(evaluate_pipeline(publisher, publisher_jobs, search), sort_keys=True))


if __name__ == "__main__":
    main()
