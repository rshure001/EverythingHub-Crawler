#!/usr/bin/env python3
"""Bounded, policy-gated expansion of page-backed catalog seeds.

One run examines at most ten approved catalog landing pages. It extracts only
same-origin article links and their visible titles from metadata-only listing
pages; it never fetches child pages, full text, media, or bulk assets.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from catalog_source_policy import (
    SourcePolicyError,
    _purpose_path_allowed,
    policy_for_host,
    request_source_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "data" / "page-catalog-seeds.json.gz"
STATE = ROOT / "data" / "page-catalog-expansion.json.gz"
MAX_BATCH = 10
MAX_CHILDREN_PER_PAGE = 200
MAX_HTML_BYTES = 1_500_000


def _read_payload(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _write_payload(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    if path.suffix == ".gz":
        temp.write_bytes(gzip.compress(raw, mtime=0))
    else:
        temp.write_bytes(raw)
    temp.replace(path)


def _eligible_seed(seed: object) -> bool:
    if not isinstance(seed, dict):
        return False
    url = seed.get("source_url") or seed.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password:
        return False
    policy = policy_for_host(parsed.hostname)
    if not policy or policy.get("discovery_allowed") is not True:
        return False
    if policy.get("terms_status") not in {"reviewed", "reviewed_per_dataset"}:
        return False
    if policy.get("metadata_only") is not True:
        return False
    fields = policy.get("allowed_metadata_fields")
    prohibited = policy.get("prohibited_content")
    if not isinstance(fields, list) or "article_url" not in fields or "title" not in fields:
        return False
    if not isinstance(prohibited, list) or not prohibited:
        return False
    return _purpose_path_allowed(parsed.path, policy, "landing_page")


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "a":
            href = values.get("href")
            self._anchor_href = href if isinstance(href, str) else None
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() == "a" and self._anchor_href:
            label = " ".join("".join(self._anchor_parts).split())[:300]
            self.links.append((self._anchor_href, label))
            self._anchor_href = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)


def _canonical_child(parent_url: str, href: str) -> str | None:
    try:
        candidate = urljoin(parent_url, href)
        parsed = urlsplit(candidate)
        parent = urlsplit(parent_url)
        _ = parsed.port
    except ValueError:
        return None
    # Require the exact HTTPS origin, not merely the same hostname.
    # Non-default ports are intentionally excluded from metadata discovery.
    parsed_port = parsed.port or 443
    parent_port = parent.port or 443
    if (
        parsed.scheme != "https"
        or parent.scheme != "https"
        or parsed_port != 443
        or parent_port != 443
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname.lower().rstrip(".") != (parent.hostname or "").lower().rstrip(".")
    ):
        return None
    if parsed.path == parent.path or not parsed.path.startswith(parent.path.rstrip("/") + "/"):
        return None
    if "/articles/" not in parsed.path and not parsed.path.rstrip("/").endswith("/article"):
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def extract_child_records(html: str, source_url: str, seed_id: str) -> list[dict]:
    parser = _ListingParser()
    parser.feed(html)
    records = []
    seen = set()
    for href, label in parser.links:
        url = _canonical_child(source_url, href)
        if not url or not label or url in seen:
            continue
        seen.add(url)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        records.append({
            "record_id": "page-child:" + digest,
            "catalog_seed_id": seed_id,
            "title": label,
            "article_url": url,
            "source_url": source_url,
            "source_host": urlsplit(source_url).hostname,
            "record_type": "article_link_metadata",
            "rights_status": "per_record_review_required",
            "verified": False,
            "content_fetched": False,
        })
        if len(records) >= MAX_CHILDREN_PER_PAGE:
            break
    return records


def run_batch(seed_path: Path = SEEDS, state_path: Path = STATE, limit: int = MAX_BATCH) -> dict:
    if not 1 <= limit <= MAX_BATCH:
        raise ValueError(f"batch limit must be between 1 and {MAX_BATCH}")
    source = _read_payload(seed_path)
    seeds = source.get("pages", [])
    if not isinstance(seeds, list):
        seeds = []
    eligible = [seed for seed in seeds if _eligible_seed(seed)]
    state = _read_payload(state_path)
    prior_records = state.get("records", [])
    if not isinstance(prior_records, list):
        prior_records = []
    records_by_id = {
        row["record_id"]: row for row in prior_records
        if isinstance(row, dict) and isinstance(row.get("record_id"), str)
    }
    outcomes = state.get("seed_outcomes", {})
    if not isinstance(outcomes, dict):
        outcomes = {}
    cursor = int(state.get("next_seed_offset", 0) or 0)
    if eligible:
        cursor %= len(eligible)
        batch = []
        scanned = 0
        while scanned < len(eligible) and len(batch) < limit:
            seed = eligible[(cursor + scanned) % len(eligible)]
            seed_id = seed.get("catalog_seed_id") if isinstance(seed, dict) else None
            prior = outcomes.get(seed_id) if isinstance(seed_id, str) else None
            # Successful listing pages are immutable inputs to this staging run;
            # don't re-request them on every scheduled cycle. Failed attempts remain retryable.
            if not isinstance(prior, dict) or prior.get("status") != "metadata_listing_processed":
                batch.append(seed)
            scanned += 1
        next_cursor = (cursor + scanned) % len(eligible)
    else:
        batch, next_cursor = [], 0

    processed = 0
    discovered = 0
    for seed in batch:
        seed_id = seed.get("catalog_seed_id")
        url = seed.get("source_url") or seed.get("url")
        if not isinstance(seed_id, str) or not seed_id or not isinstance(url, str):
            continue
        try:
            raw, status, headers, policy = request_source_bytes(
                url, purpose="landing_page", max_bytes=MAX_HTML_BYTES,
                timeout=10, accept="text/html",
            )
            if status != 200:
                outcomes[seed_id] = {"status": "http_error", "http_status": status, "checked_at": datetime.now(timezone.utc).isoformat()}
                continue
            charset = "utf-8"
            content_type = headers.get("Content-Type", "")
            if "charset=" in content_type.lower():
                charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
            html = raw.decode(charset, errors="replace")
            children = extract_child_records(html, url, seed_id)
            before = len(records_by_id)
            for record in children:
                records_by_id.setdefault(record["record_id"], record)
            added = len(records_by_id) - before
            discovered += added
            processed += 1
            outcomes[seed_id] = {
                "status": "metadata_listing_processed",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "child_link_candidates": len(children),
                "new_unique_child_records": added,
                "terms_url": policy.get("terms_url"),
                "terms_status": policy.get("terms_status"),
            }
        except SourcePolicyError as exc:
            outcomes[seed_id] = {"status": "policy_or_robots_blocked", "reason": str(exc)[:240]}
        except (OSError, UnicodeError, ValueError, TimeoutError) as exc:
            outcomes[seed_id] = {"status": "retryable_error", "reason": type(exc).__name__}

    result = {
        "schema_version": 1,
        "status": "metadata_only_staging_not_production",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_limit": limit,
        "eligible_page_seeds": len(eligible),
        "last_batch_size": len(batch),
        "last_batch_processed": processed,
        "last_batch_new_unique_child_records": discovered,
        "next_seed_offset": next_cursor,
        "seed_outcomes": outcomes,
        "records": list(records_by_id.values()),
        "metrics": {
            "unique_child_records_staged": len(records_by_id),
            "seeds_processed": sum(1 for x in outcomes.values() if isinstance(x, dict) and x.get("status") == "metadata_listing_processed"),
            "verified_catalogs": 0,
            "records_published": 0,
            "child_pages_fetched": 0,
            "full_text_fetched": 0,
            "media_fetched": 0,
        },
    }
    _write_payload(state_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=Path, default=SEEDS)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    args = parser.parse_args()
    result = run_batch(args.seeds, args.state, args.limit)
    print(json.dumps({key: result[key] for key in ("eligible_page_seeds", "last_batch_size", "last_batch_processed", "last_batch_new_unique_child_records", "metrics")}, indent=2))


if __name__ == "__main__":
    main()
