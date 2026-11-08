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
    lines = [
        "## Everything Hub crawler health",
        "",
        f"- Total indexed page records: **{len(pages):,}**",
        f"- Unique indexed hosts: **{len(hosts):,}**",
        f"- URLs discovered (frontier seen): **{len(frontier.get('seen', [])):,}**",
        f"- URLs waiting in frontier: **{len(frontier.get('queue', [])):,}**",
        f"- Fetched this run: **{index.get('pages_crawled_this_run', 0):,}**",
        f"- Parsed/indexed this run: **{index.get('pages_indexed_this_run', 0):,}**",
        f"- Last index update (UTC): **{updated_text}**",
        "",
        "### Indexed records by crawl depth",
        "",
    ]
    lines.extend(f"- Depth {depth}: {count:,}" for depth, count in sorted(depths.items()))
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
