#!/usr/bin/env python3
"""Read-only preflight for source-approved media extraction.

This module never fetches media, invokes an extractor, or changes reuse rights.
It reports only aggregate counts; candidate URLs are not written to the summary.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from media_discovery import MEDIA_TYPES, safe_media_url

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "media-discovery.json"
POLICY_REGISTRY = ROOT / "crawler" / "media-source-terms.json"
MAX_CATALOG_BYTES = 20_000_000
MAX_POLICY_BYTES = 1_000_000
MAX_POLICY_REVIEWS = 2_000
MAX_ROBOTS_AGE_SECONDS = 24 * 60 * 60
MAX_TERMS_REVIEW_AGE_SECONDS = 90 * 24 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _origin(value: Any):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        host = parsed.hostname.casefold().rstrip(".")
        if host in {"localhost", "localhost.localdomain"}:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return parsed.scheme.casefold(), host, port
    except (TypeError, ValueError):
        return None


def _public_canonical_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    normalized = safe_media_url("", value)
    return bool(normalized) and normalized == value


def _aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        raw = value.strip()
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _epoch(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _compile_reviews(registry: Any, now: datetime) -> tuple[dict, set, set, set, dict]:
    if not isinstance(registry, dict) or registry.get("version") != 1:
        raise ValueError("source-terms registry must be a version 1 object")
    reviews = registry.get("reviews")
    if not isinstance(reviews, list) or len(reviews) > MAX_POLICY_REVIEWS:
        raise ValueError("source-terms registry reviews must be a bounded array")

    approved: dict[tuple[str, str, int], dict] = {}
    denied: set[tuple[str, str, int]] = set()
    invalid: set[tuple[str, str, int]] = set()
    ambiguous: set[tuple[str, str, int]] = set()
    seen: set[tuple[str, str, int]] = set()
    counts = {"valid_approvals": 0, "denials": 0, "invalid_reviews": 0, "duplicate_origins": 0}

    for row in reviews:
        if not isinstance(row, Mapping):
            counts["invalid_reviews"] += 1
            continue
        origin = _origin(row.get("origin"))
        if origin is None:
            counts["invalid_reviews"] += 1
            continue
        if origin in seen:
            approved.pop(origin, None)
            denied.discard(origin)
            invalid.discard(origin)
            ambiguous.add(origin)
            counts["duplicate_origins"] += 1
            continue
        seen.add(origin)

        if row.get("extraction_permission") is not True or row.get("source_terms_status") != "allow_extraction":
            denied.add(origin)
            counts["denials"] += 1
            continue

        terms_url = row.get("source_terms_url")
        digest = row.get("source_terms_sha256")
        checked_at = _aware_timestamp(row.get("source_terms_checked_at"))
        if (
            not _public_canonical_url(terms_url)
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or checked_at is None
        ):
            invalid.add(origin)
            counts["invalid_reviews"] += 1
            continue
        age = (now - checked_at).total_seconds()
        if age < -MAX_FUTURE_SKEW_SECONDS or age > MAX_TERMS_REVIEW_AGE_SECONDS:
            invalid.add(origin)
            counts["invalid_reviews"] += 1
            continue
        approved[origin] = {
            "source_terms_url": terms_url,
            "source_terms_sha256": digest,
            "source_terms_checked_at": checked_at.isoformat(),
        }
        counts["valid_approvals"] += 1

    for origin in ambiguous:
        approved.pop(origin, None)
        denied.discard(origin)
        invalid.discard(origin)
    return approved, denied, invalid, ambiguous, counts


def evaluate_preflight(catalog: Any, registry: Any, *, now: datetime | None = None,
                       registry_missing: bool = False) -> dict:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("preflight clock must be timezone-aware")
    current = current.astimezone(timezone.utc)

    if not isinstance(catalog, dict) or catalog.get("version") != 1:
        raise ValueError("media catalog must be a version 1 object")
    candidates = catalog.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("media catalog candidates must be an array")
    if len(candidates) > 50_000:
        raise ValueError("media catalog exceeds the candidate safety limit")

    approved, denied, invalid, ambiguous, review_counts = _compile_reviews(registry, current)
    deferred = Counter()
    eligible_by_type = Counter()
    eligible = 0
    robots_fresh = 0
    rights_unknown = 0

    for row in candidates:
        if not isinstance(row, Mapping):
            deferred["invalid_candidate"] += 1
            continue
        kind = row.get("media_type")
        if kind not in MEDIA_TYPES:
            deferred["unsupported_media_type"] += 1
            continue
        if row.get("robots_allowed") is not True:
            deferred["robots_not_allowed"] += 1
            continue
        if row.get("policy_status") != "robots_allowed_not_fetched":
            deferred["not_metadata_only"] += 1
            continue

        media_url = row.get("url")
        source_url = row.get("source_page_url")
        media_origin = _origin(media_url)
        source_origin = _origin(source_url)
        if (
            not _public_canonical_url(media_url)
            or not _public_canonical_url(source_url)
            or media_origin is None
            or media_origin != source_origin
        ):
            deferred["unsafe_or_cross_origin_url"] += 1
            continue

        robots_checked = _epoch(row.get("robots_checked_at"))
        if robots_checked is None:
            deferred["missing_robots_check_timestamp"] += 1
            continue
        robots_age = current.timestamp() - robots_checked
        if robots_age < -MAX_FUTURE_SKEW_SECONDS:
            deferred["future_robots_check"] += 1
            continue
        if robots_age > MAX_ROBOTS_AGE_SECONDS:
            deferred["stale_robots_check"] += 1
            continue
        robots_fresh += 1

        if media_origin in ambiguous:
            deferred["ambiguous_source_terms_reviews"] += 1
        elif media_origin in denied:
            deferred["source_terms_denied"] += 1
        elif media_origin in invalid:
            deferred["invalid_or_stale_source_terms_review"] += 1
        elif media_origin not in approved:
            deferred["missing_source_terms_approval"] += 1
        else:
            eligible += 1
            eligible_by_type[kind] += 1

        if row.get("rights_status") == "unknown" or row.get("usage_rights") == "Unknown":
            rights_unknown += 1

    return {
        "catalog_candidates": len(candidates),
        "robots_fresh_candidates": robots_fresh,
        "eligible_for_extraction_preflight": eligible,
        "eligible_by_type": dict(sorted(eligible_by_type.items())),
        "deferred_by_reason": dict(sorted(deferred.items())),
        "rights_unknown_candidates": rights_unknown,
        "registry_missing": bool(registry_missing),
        "registry_reviews": review_counts,
        "media_downloads": 0,
        "extractions_performed": 0,
        "redistribution_authorizations_granted": 0,
        "note": "Preflight only. No media was fetched, no extractor was invoked, and extraction approval does not grant redistribution rights.",
    }


def _load_json(path: Path, max_bytes: int) -> Any:
    if not path.is_file():
        return None
    if path.stat().st_size > max_bytes:
        raise ValueError(f"{path.name} exceeds its safety size limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path.name} is unreadable or invalid JSON: {error}") from error


def main() -> None:
    catalog = _load_json(CATALOG, MAX_CATALOG_BYTES)
    if catalog is None:
        raise SystemExit("media catalog is missing")
    registry = _load_json(POLICY_REGISTRY, MAX_POLICY_BYTES)
    registry_missing = registry is None
    if registry_missing:
        registry = {"version": 1, "reviews": []}
    report = evaluate_preflight(catalog, registry, registry_missing=registry_missing)
    lines = [
        "## Hurricane media extraction preflight",
        "",
        f"- Metadata candidates: **{report['catalog_candidates']:,}**",
        f"- Candidates with a fresh robots decision: **{report['robots_fresh_candidates']:,}**",
        f"- Eligible for extraction preflight: **{report['eligible_for_extraction_preflight']:,}**",
        f"- Candidates whose reuse rights remain unknown: **{report['rights_unknown_candidates']:,}**",
        f"- Valid source-term approvals: **{report['registry_reviews']['valid_approvals']:,}**",
        f"- Denied source-term reviews: **{report['registry_reviews']['denials']:,}**",
        f"- Invalid or stale source-term reviews: **{report['registry_reviews']['invalid_reviews']:,}**",
        f"- Duplicate/ambiguous source origins: **{report['registry_reviews']['duplicate_origins']:,}**",
        f"- Policy registry missing: **{'yes' if report['registry_missing'] else 'no'}**",
        "",
        "### Deferred candidates",
        "",
    ]
    lines.extend(f"- {reason}: **{count:,}**" for reason, count in report["deferred_by_reason"].items())
    lines.extend([
        "",
        "No media was downloaded, no extractor was invoked, and no redistribution permission was granted.",
        "",
    ])
    rendered = "\n".join(lines)
    print(rendered, end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(rendered)


if __name__ == "__main__":
    main()
