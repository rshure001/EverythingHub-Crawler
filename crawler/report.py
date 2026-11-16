#!/usr/bin/env python3
"""Summarize crawler health without exposing page contents."""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "index.json"
FRONTIER = ROOT / "data" / "frontier.json"

def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)

def main():
    index = load(INDEX)
    frontier = load(FRONTIER)
    pages = [p for p in index.get("pages", []) if isinstance(p, dict) and p.get("url")]
    hosts = Counter(urlsplit(p["url"]).netloc.lower() for p in pages)
    depths = Counter(str(p.get("depth", "unknown")) for p in pages)
    updated = index.get("updated_at")
    updated_text = datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated else "unknown"
    duration = float(index.get("run_duration_seconds", 0) or 0)
    indexed_this_run = int(index.get("pages_indexed_this_run", 0) or 0)
    indexed_rate = indexed_this_run / (duration / 60) if duration > 0 else 0
    host_requests = index.get("host_requests_this_run", {}) or {}
    lines = [
        "## Everything Hub crawler health",
        "",
        f"- Total indexed page records: **{len(pages):,}**",
        f"- Unique indexed hosts: **{len(hosts):,}**",
        f"- URLs discovered (frontier seen): **{len(frontier.get('seen', [])):,}**",
        f"- URLs waiting in frontier: **{len(frontier.get('queue', [])):,}**",
        f"- Fetched this run: **{index.get('pages_crawled_this_run', 0):,}**",
        f"- Parsed/indexed this run: **{indexed_this_run:,}**",
        f"- Not indexed this run: **{int(index.get('pages_not_indexed_this_run', 0) or 0):,}**",
        f"- Robots-blocked URLs this run: **{int(index.get('robots_blocked_this_run', 0) or 0):,}**",
        f"- Run duration: **{duration:.1f} seconds**",
        f"- Measured indexed rate (this run): **{indexed_rate:,.1f} pages/minute**",
        f"- Last index update (UTC): **{updated_text}**",
        "",
        "### Indexed records by crawl depth",
        "",
    ]
    lines.extend(f"- Depth {depth}: {count:,}" for depth, count in sorted(depths.items()))
    lines += ["", "### Requests by host this run", ""]
    lines.extend(f"- {host}: {count:,}" for host, count in sorted(host_requests.items(), key=lambda item: (-item[1], item[0]))[:10])
    lines += ["", "### Largest indexed hosts", ""]
    lines.extend(f"- {host}: {count:,}" for host, count in hosts.most_common(10))
    report = "\n".join(lines) + "\n"
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)

if __name__ == "__main__":
    main()
