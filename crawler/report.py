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
from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "index.json"
FRONTIER = ROOT / "data" / "frontier.json"
SEEDS = ROOT / "crawler" / "seeds.txt"


def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _http_hostname(value):
    """Return a normalized HTTP(S) hostname, or None for malformed input."""
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        return (parsed.hostname or "").casefold().rstrip(".") or None
    except (TypeError, ValueError):
        return None


def summarize_seed_coverage(seed_urls, pages):
    """Compare configured seed hosts with hosts represented by indexed pages."""
    seed_hosts = {
        host for url in seed_urls
        if (host := _http_hostname(url))
    }
    indexed_hosts = {
        host for page in pages
        if isinstance(page, dict) and (host := _http_hostname(page.get("url")))
    }
    missing = sorted(seed_hosts - indexed_hosts)
    return {
        "configured": len(seed_hosts),
        "indexed": len(seed_hosts & indexed_hosts),
        "missing": missing,
    }


def main():
    index = load_index(INDEX)
    frontier = load(FRONTIER)
    pages = [p for p in index.get("pages", []) if isinstance(p, dict) and p.get("url")]
    seed_urls = [
        line.strip()
        for line in SEEDS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    seed_coverage = summarize_seed_coverage(seed_urls, pages)
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
    indexed_hourly_equivalent = indexed_this_run / (duration / 3600) if duration > 0 else 0
    manifest_unique_delta = None
    unique_rate = None
    before_pages = os.environ.get("EH_MANIFEST_BEFORE_PAGES")
    manifest_path = ROOT / "public-search" / "manifest.json"
    if before_pages is not None and duration > 0 and manifest_path.exists():
        try:
            after_manifest = load(manifest_path)
            manifest_unique_delta = int(after_manifest["total_pages"]) - int(before_pages)
            unique_rate = manifest_unique_delta / (duration / 60)
        except (KeyError, TypeError, ValueError, OSError):
            manifest_unique_delta = None
            unique_rate = None
    hourly_target = 60000
    unique_hourly_equivalent = unique_rate * 60 if unique_rate is not None else None
    hourly_target_status = (
        "above target pace" if unique_hourly_equivalent is not None
        and unique_hourly_equivalent >= hourly_target else "below target pace"
        if unique_hourly_equivalent is not None else "not measured"
    )
    host_requests = index.get("host_requests_this_run", {}) or {}
    fetch_attempts = int(index.get("fetch_attempts_this_run", index.get("pages_crawled_this_run", 0)) or 0)
    successful_http = int(index.get("successful_http_responses_this_run", 0) or 0)
    html_parsed = int(index.get("html_pages_parsed_this_run", indexed_this_run) or 0)
    non_html = int(index.get("non_html_responses_this_run", 0) or 0)
    fetch_errors = int(index.get("fetch_or_parse_errors_this_run", 0) or 0)
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
        f"- Configured seed hosts: **{seed_coverage['configured']:,}**",
        f"- Configured seed hosts represented in the index: **{seed_coverage['indexed']:,}**",
        f"- Configured seed hosts not yet represented: **{len(seed_coverage['missing']):,}**",
        f"- Robots-allowed media candidates retained (not fetched): **{int(index.get('media_candidates_total', 0) or 0):,}**",
        f"- Same-origin, robots-allowed media candidates admitted this run: **{int(index.get('media_candidates_discovered_this_run', 0) or 0):,}**",
        f"- New unique media candidates this run: **{int(index.get('media_candidates_added_this_run', 0) or 0):,}**",
        f"- Media candidates blocked by robots.txt: **{int(index.get('media_candidates_robots_blocked_this_run', 0) or 0):,}**",
        f"- Cross-origin media candidates deferred: **{int(index.get('media_external_candidates_deferred_this_run', 0) or 0):,}**",
        f"- URLs discovered (frontier seen): **{len(frontier.get('seen', [])):,}**",
        f"- URLs waiting in frontier: **{len(frontier.get('queue', [])):,}**",
        f"- Fetch attempts this run: **{fetch_attempts:,}**",
        f"- Successful HTTP responses: **{successful_http:,}**",
        f"- HTML pages parsed: **{html_parsed:,}**",
        f"- Non-HTML responses: **{non_html:,}**",
        f"- Fetch or parse errors: **{fetch_errors:,}**",
        f"- Indexed records this run: **{indexed_this_run:,}**",
        f"- Not indexed this run: **{int(index.get('pages_not_indexed_this_run', 0) or 0):,}**",
        f"- Robots-blocked URLs this run: **{int(index.get('robots_blocked_this_run', 0) or 0):,}**",
        f"- Noindex pages excluded this run: **{int(index.get('index_policy_blocked_this_run', 0) or 0):,}**",
        f"- Access-challenge pages excluded this run: **{int(index.get('access_challenge_pages_this_run', 0) or 0):,}**",
        f"- Existing access-challenge records removed: **{int(index.get('access_challenge_pages_removed_from_existing_index', 0) or 0):,}**",
        f"- Nofollow pages not expanded this run: **{int(index.get('nofollow_pages_this_run', 0) or 0):,}**",
        f"- Robots.txt policy fetch failures (fail-closed): **{int(index.get('robots_policy_failures_this_run', 0) or 0):,}**",
        f"- Rate-limited responses this run: **{int(index.get('rate_limited_responses_this_run', 0) or 0):,}**",
        f"- Transient retries queued this run: **{int(index.get('transient_retries_queued_this_run', 0) or 0):,}**",
        f"- Sitemap hosts checked this run: **{int(index.get('sitemap_hosts_checked_this_run', 0) or 0):,}**",
        f"- Sitemap files requested this run: **{int(index.get('sitemap_fetches_this_run', 0) or 0):,}**",
        f"- URLs added from sitemaps this run: **{int(index.get('sitemap_urls_added_this_run', 0) or 0):,}**",
        f"- Run duration: **{duration:.1f} seconds**",
        f"- Processed/upserted record rate (not necessarily unique): **{indexed_rate:,.1f}/minute**",
        f"- Processed/upserted hourly equivalent (not unique): **{indexed_hourly_equivalent:,.0f}/hour**",
        (
            f"- Net new unique pages this run: **{manifest_unique_delta:+,}**; "
            f"active-run unique rate: **{unique_rate:,.1f}/minute** "
            f"(**{unique_hourly_equivalent:,.0f}/hour equivalent**)"
            if unique_rate is not None
            else "- Net new unique pages this run: **not measured**"
        ),
        f"- 1,000-new-unique-pages/minute target (60,000/hour): **{hourly_target_status}**",
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
    lines += ["", "### Seed host coverage", ""]
    if seed_coverage["missing"]:
        lines.append("- Not yet represented: " + ", ".join(seed_coverage["missing"]))
    else:
        lines.append("- All configured seed hosts have at least one indexed page.")
    report = "\n".join(lines) + "\n"
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)


if __name__ == "__main__":
    main()
