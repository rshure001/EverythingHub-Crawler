#!/usr/bin/env python3
"""Summarize crawler health without exposing page contents."""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from lane_classifier import VALID_LANES, classify_lane
from crawl import is_access_challenge_page

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
    lanes = Counter(
        p.get("lane") if isinstance(p.get("lane"), str) and p.get("lane") in VALID_LANES
        else classify_lane(p.get("url", ""))
        for p in pages
    )
    blank_titles = sum(not str(p.get("title") or "").strip() for p in pages)
    thin_content = sum(
        len(" ".join(str(p.get(field) or "") for field in ("title", "description", "text")).strip()) < 80
        for p in pages
    )
    challenge_pages = sum(
        is_access_challenge_page(p.get("title"), p.get("description"), p.get("text"))
        for p in pages
    )
    updated = index.get("updated_at")
    updated_text = datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated else "unknown"
    duration = float(index.get("run_duration_seconds", 0) or 0)
    indexed_this_run = int(index.get("pages_indexed_this_run", 0) or 0)
    indexed_rate = indexed_this_run / (duration / 60) if duration > 0 else 0
    host_requests = index.get("host_requests_this_run", {}) or {}
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "local")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "local")
    commit_sha = os.environ.get("GITHUB_SHA", "local")[:12]
    lines = [
        "## Everything Hub crawler health",
        "",
        f"- GitHub workflow run: **{run_number}** (ID `{run_id}`, attempt {run_attempt})",
        f"- Trigger: **{event_name}**",
        f"- Source commit: `{commit_sha}`",
        "",
        f"- Total indexed page records: **{len(pages):,}**",
        f"- Unique indexed hosts: **{len(hosts):,}**",
        f"- URLs discovered (frontier seen): **{len(frontier.get('seen', [])):,}**",
        f"- URLs waiting in frontier: **{len(frontier.get('queue', [])):,}**",
        f"- Fetched this run: **{index.get('pages_crawled_this_run', 0):,}**",
        f"- Parsed/indexed this run: **{indexed_this_run:,}**",
        f"- Not indexed this run: **{int(index.get('pages_not_indexed_this_run', 0) or 0):,}**",
        f"- Robots-blocked URLs this run: **{int(index.get('robots_blocked_this_run', 0) or 0):,}**",
        f"- Noindex pages excluded this run: **{int(index.get('index_policy_blocked_this_run', 0) or 0):,}**",
        f"- Access-challenge pages excluded this run: **{int(index.get('access_challenge_pages_this_run', 0) or 0):,}**",
        f"- Nofollow pages not expanded this run: **{int(index.get('nofollow_pages_this_run', 0) or 0):,}**",
        f"- Robots.txt policy fetch failures (fail-closed): **{int(index.get('robots_policy_failures_this_run', 0) or 0):,}**",
        f"- Rate-limited responses this run: **{int(index.get('rate_limited_responses_this_run', 0) or 0):,}**",
        f"- Transient retries queued this run: **{int(index.get('transient_retries_queued_this_run', 0) or 0):,}**",
        f"- Sitemap hosts checked this run: **{int(index.get('sitemap_hosts_checked_this_run', 0) or 0):,}**",
        f"- Sitemap files requested this run: **{int(index.get('sitemap_fetches_this_run', 0) or 0):,}**",
        f"- URLs added from sitemaps this run: **{int(index.get('sitemap_urls_added_this_run', 0) or 0):,}**",
        f"- Run duration: **{duration:.1f} seconds**",
        f"- Measured indexed rate (this run): **{indexed_rate:,.1f} pages/minute**",
        f"- Last index update (UTC): **{updated_text}**",
        "",
        "### Indexed content quality",
        "",
        f"- Records with blank titles: **{blank_titles:,}**",
        f"- Records with fewer than 80 characters of combined text/metadata: **{thin_content:,}**",
        f"- Records matching short challenge/interstitial patterns: **{challenge_pages:,}**",
        "",
        "### Indexed records by crawl depth",
        "",
    ]
    lines.extend(f"- Depth {depth}: {count:,}" for depth, count in sorted(depths.items()))
    lines += ["", "### Indexed records by search lane", ""]
    lines.extend(f"- {lane}: {count:,}" for lane, count in sorted(lanes.items()))
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
