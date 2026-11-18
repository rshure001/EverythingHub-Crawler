#!/usr/bin/env python3
"""Evidence-based Hurricane Core maturity and operating metrics."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _timestamp(value):
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except (TypeError, ValueError, OverflowError):
            return None


def _host(value):
    try:
        return (urlsplit(str(value)).hostname or "").casefold() or None
    except (TypeError, ValueError):
        return None


def evaluate(index: dict, frontier: dict, now: int | None = None,
             media_catalog: dict | None = None) -> dict:
    index = index if isinstance(index, dict) else {}
    frontier = frontier if isinstance(frontier, dict) else {}
    raw_pages = index.get("pages", [])
    pages = [p for p in raw_pages if isinstance(p, dict) and p.get("url")] if isinstance(raw_pages, list) else []
    hosts = {host for p in pages if (host := _host(p.get("url")))}
    raw_seen = frontier.get("seen", [])
    raw_queue = frontier.get("queue", [])
    discovered = len(raw_seen) if isinstance(raw_seen, (list, tuple, set)) else 0
    queued = len(raw_queue) if isinstance(raw_queue, (list, tuple)) else 0
    updated = _timestamp(index.get("updated_at"))
    current = _timestamp(now) if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = max(0, current - updated) if updated is not None else None

    feedback = _count(index.get("feedback_events_total"))
    extractions = _count(index.get("media_extractions_total"))
    index_candidates = _count(index.get("media_candidates_total"))
    discovered_candidates = _count(index.get("media_candidates_discovered_this_run"))
    added_candidates = _count(index.get("media_candidates_added_this_run"))
    blocked_candidates = _count(index.get("media_candidates_robots_blocked_this_run"))
    deferred_candidates = _count(index.get("media_external_candidates_deferred_this_run"))

    catalog_candidates = []
    if isinstance(media_catalog, dict) and isinstance(media_catalog.get("candidates"), list):
        catalog_candidates = [row for row in media_catalog["candidates"] if isinstance(row, dict)]
    type_counts = Counter(
        str(row.get("media_type"))
        for row in catalog_candidates
        if row.get("media_type") in {"image", "audio", "video"}
    )
    unknown_rights = sum(
        1 for row in catalog_candidates
        if str(row.get("rights_status", "")).casefold() == "unknown"
        or str(row.get("usage_rights", "")).casefold() == "unknown"
    )
    robots_allowed = sum(1 for row in catalog_candidates if row.get("robots_allowed") is True)
    catalog_count = len(catalog_candidates)
    candidate_total = catalog_count if isinstance(media_catalog, dict) else index_candidates

    gates = [
        (1, len(pages) >= 1, "crawler has indexed its first page"),
        (2, len(pages) >= 500 and len(hosts) >= 5, "500 indexed pages across 5 hosts"),
        (3, len(pages) >= 2500 and len(hosts) >= 20 and discovered >= len(pages), "2,500 indexed pages across 20 hosts with tracked discovery frontier"),
        (4, len(pages) >= 5000 and len(hosts) >= 35 and feedback >= 1, "5,000 pages across 35 hosts plus at least one persisted learning-feedback event"),
        (5, len(pages) >= 10000 and len(hosts) >= 49 and feedback >= 100 and extractions >= 1 and age is not None and age <= 600, "10,000 pages across 49 hosts, 100 persisted feedback events, at least one actual media extraction, and an index refreshed within 10 minutes"),
    ]
    category = 0
    for level, passed, _ in gates:
        if passed:
            category = level
        else:
            break
    next_gate = next((description for level, passed, description in gates if level == category + 1), "all five maturity gates passed")
    return {
        "name": "Hurricane Core",
        "category": category,
        "category_label": f"Category {category}" if category else "Not yet operational",
        "indexed_pages": len(pages),
        "unique_hosts": len(hosts),
        "urls_discovered": discovered,
        "frontier_waiting": queued,
        "feedback_events": feedback,
        "media_extractions": extractions,
        "media_candidates_total": candidate_total,
        "media_candidates_index_total": index_candidates,
        "media_candidates_discovered_this_run": discovered_candidates,
        "media_candidates_added_this_run": added_candidates,
        "media_candidates_robots_blocked_this_run": blocked_candidates,
        "media_external_candidates_deferred_this_run": deferred_candidates,
        "media_catalog_by_type": dict(sorted(type_counts.items())),
        "media_catalog_unknown_rights": unknown_rights,
        "media_catalog_robots_allowed": robots_allowed,
        "media_catalog_count_mismatch": (
            isinstance(media_catalog, dict) and index_candidates != catalog_count
        ),
        "index_age_seconds": age,
        "next_gate": next_gate,
        "gates": [{"category": level, "passed": passed, "requirement": description} for level, passed, description in gates],
        "measurement_note": "Category advances only when every stated gate through that level is met; missing telemetry counts as zero. Discovered media candidates are metadata records, not completed media extractions.",
    }


def render(metrics: dict) -> str:
    lines = [
        "## Hurricane Core strength",
        "",
        f"- **Current stage:** {metrics['category_label']}",
        f"- Indexed pages: **{metrics['indexed_pages']:,}**",
        f"- Unique hosts: **{metrics['unique_hosts']:,}**",
        f"- URLs discovered: **{metrics['urls_discovered']:,}**",
        f"- URLs waiting: **{metrics['frontier_waiting']:,}**",
        f"- Persisted feedback events: **{metrics['feedback_events']:,}**",
        f"- Completed media extractions: **{metrics['media_extractions']:,}**",
        f"- Media candidates retained (metadata only): **{metrics['media_candidates_total']:,}**",
        f"- Media candidates discovered this run: **{metrics['media_candidates_discovered_this_run']:,}**",
        f"- New unique media candidates this run: **{metrics['media_candidates_added_this_run']:,}**",
        f"- Media candidates blocked by robots.txt: **{metrics['media_candidates_robots_blocked_this_run']:,}**",
        f"- Cross-origin media candidates deferred: **{metrics['media_external_candidates_deferred_this_run']:,}**",
    ]
    if metrics["media_catalog_by_type"]:
        types = ", ".join(f"{kind}: {count:,}" for kind, count in metrics["media_catalog_by_type"].items())
        lines.append(f"- Media catalog by type: **{types}**")
        lines.append(f"- Media candidates with unknown reuse rights: **{metrics['media_catalog_unknown_rights']:,}**")
        lines.append(f"- Media candidates with explicit robots allowance: **{metrics['media_catalog_robots_allowed']:,}**")
    if metrics["media_catalog_count_mismatch"]:
        lines.append(
            f"- **Warning:** index reports {metrics['media_candidates_index_total']:,} media candidates, "
            f"but the media catalog contains {metrics['media_candidates_total']:,}."
        )
    lines.extend([
        f"- Index age (seconds): **{metrics['index_age_seconds'] if metrics['index_age_seconds'] is not None else 'unknown'}**",
        f"- **Next milestone:** {metrics['next_gate']}",
        "",
        metrics["measurement_note"],
        "",
        "### Category gates",
        "",
    ])
    lines.extend(f"- Category {g['category']}: {'PASS' if g['passed'] else 'not yet met'} — {g['requirement']}" for g in metrics["gates"])
    return "\n".join(lines) + "\n"


def main():
    index = json.loads((ROOT / "data" / "index.json").read_text(encoding="utf-8"))
    frontier = json.loads((ROOT / "data" / "frontier.json").read_text(encoding="utf-8"))
    media_path = ROOT / "data" / "media-discovery.json"
    media_catalog = json.loads(media_path.read_text(encoding="utf-8")) if media_path.exists() else None
    report = render(evaluate(index, frontier, media_catalog=media_catalog))
    print(report, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)


if __name__ == "__main__":
    main()
