#!/usr/bin/env python3
"""Evidence-based Hurricane Core maturity and operating metrics."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit


def evaluate(index: dict, frontier: dict, now: int | None = None) -> dict:
    pages = [p for p in index.get("pages", []) if isinstance(p, dict) and p.get("url")]
    hosts = {urlsplit(p["url"]).hostname.lower() for p in pages if urlsplit(p["url"]).hostname}
    discovered = len(frontier.get("seen", []))
    queued = len(frontier.get("queue", []))
    updated = index.get("updated_at")
    age = max(0, (now if now is not None else int(datetime.now(timezone.utc).timestamp())) - int(updated)) if updated else None
    feedback = int(index.get("feedback_events_total", 0) or 0)
    extractions = int(index.get("media_extractions_total", 0) or 0)
    gates = [
        (1, len(pages) >= 1, "crawler has indexed its first page"),
        (2, len(pages) >= 500 and len(hosts) >= 5, "500 indexed pages across 5 hosts"),
        (3, len(pages) >= 2500 and len(hosts) >= 20 and discovered >= len(pages), "2,500 indexed pages across 20 hosts with tracked discovery frontier"),
        (4, len(pages) >= 5000 and len(hosts) >= 35 and feedback >= 1, "5,000 pages across 35 hosts plus at least one persisted learning-feedback event"),
        (5, len(pages) >= 10000 and len(hosts) >= 49 and feedback >= 100 and extractions >= 1 and age is not None and age <= 600, "10,000 pages across 49 hosts, 100 persisted feedback events, a real media extraction, and an index refreshed within 10 minutes"),
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
        "index_age_seconds": age,
        "next_gate": next_gate,
        "gates": [{"category": level, "passed": passed, "requirement": description} for level, passed, description in gates],
        "measurement_note": "Category advances only when every stated gate through that level is met; missing telemetry counts as zero.",
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
        f"- Media extractions completed: **{metrics['media_extractions']:,}**",
        f"- Index age (seconds): **{metrics['index_age_seconds'] if metrics['index_age_seconds'] is not None else 'unknown'}**",
        f"- **Next milestone:** {metrics['next_gate}",
        "",
        metrics["measurement_note"],
        "",
        "### Category gates",
        "",
    ]
    lines.extend(f"- Category {g['category']}: {'PASS' if g['passed'] else 'not yet met'} — {g['requirement']}" for g in metrics["gates"])
    return "\n".join(lines) + "\n"
