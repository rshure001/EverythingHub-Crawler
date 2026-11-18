#!/usr/bin/env python3
"""Validate the metadata-only media catalog before indexing or publishing.

This is a local, read-only check. It never fetches media or changes reuse rights.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from media_discovery import MAX_STORED_CANDIDATES, MAX_SOURCE_PAGES, MEDIA_TYPES, safe_media_url

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "media-discovery.json"
MAX_CATALOG_BYTES = 20_000_000
MAX_TITLE_LENGTH = 300
MAX_ALT_LENGTH = 500
MAX_SEEN_COUNT = 1_000_000


def _origin(value: str):
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        return parsed.scheme.casefold(), parsed.hostname.casefold().rstrip("."), port
    except (TypeError, ValueError):
        return None


def _canonical_public_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    normalized = safe_media_url("", value)
    return bool(normalized) and normalized == value


def _nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_document(document: Any) -> tuple[list[str], dict[str, Any]]:
    """Return policy/schema errors and bounded aggregate metrics."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["catalog root must be an object"], {}
    if document.get("version") != 1:
        errors.append("catalog version must be 1")
    if not _nonnegative_integer(document.get("updated_at")) or document.get("updated_at", 0) == 0:
        errors.append("catalog updated_at must be a positive integer")

    rows = document.get("candidates")
    if not isinstance(rows, list):
        return errors + ["candidates must be an array"], {}
    if len(rows) > MAX_STORED_CANDIDATES:
        errors.append("catalog exceeds the configured candidate limit")
    if document.get("total_candidates") != len(rows):
        errors.append("total_candidates does not match the candidates array")

    seen: set[tuple[str, str]] = set()
    types: Counter[str] = Counter()
    origins: set[tuple[str, str, int]] = set()
    robots_allowed = 0
    rights_unknown = 0
    not_fetched = 0

    for index, row in enumerate(rows):
        prefix = f"candidate[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue

        kind = row.get("media_type")
        if not isinstance(kind, str) or kind not in MEDIA_TYPES:
            errors.append(f"{prefix} has an unsupported media_type")
            continue
        types[kind] += 1

        media_url = row.get("url")
        source_url = row.get("source_page_url")
        if not _canonical_public_url(media_url):
            errors.append(f"{prefix} has an unsafe or non-canonical media URL")
            continue
        if not _canonical_public_url(source_url):
            errors.append(f"{prefix} has an unsafe or non-canonical source-page URL")
            continue

        media_origin = _origin(media_url)
        source_origin = _origin(source_url)
        if not media_origin or media_origin != source_origin:
            errors.append(f"{prefix} media URL is not same-origin with its source page")
        elif media_origin:
            origins.add(media_origin)

        key = (kind, media_url)
        if key in seen:
            errors.append(f"{prefix} duplicates an earlier media candidate")
        seen.add(key)

        source_pages = row.get("source_pages")
        if (
            not isinstance(source_pages, list)
            or not source_pages
            or len(source_pages) > MAX_SOURCE_PAGES
        ):
            errors.append(f"{prefix} source_pages must contain 1 to {MAX_SOURCE_PAGES} URLs")
        else:
            if not all(isinstance(source_page, str) for source_page in source_pages):
                errors.append(f"{prefix} source_pages entries must be strings")
            else:
                if len(set(source_pages)) != len(source_pages):
                    errors.append(f"{prefix} source_pages contains duplicates")
                if source_url not in source_pages:
                    errors.append(f"{prefix} source_pages must include source_page_url")
                for source_page in source_pages:
                    if not _canonical_public_url(source_page) or _origin(source_page) != media_origin:
                        errors.append(f"{prefix} contains an unsafe or cross-origin source page")
                        break

        title = row.get("title", "")
        alt_text = row.get("alt_text", "")
        if not isinstance(title, str) or len(title) > MAX_TITLE_LENGTH:
            errors.append(f"{prefix} title must be text of at most {MAX_TITLE_LENGTH} characters")
        if not isinstance(alt_text, str) or len(alt_text) > MAX_ALT_LENGTH:
            errors.append(f"{prefix} alt_text must be text of at most {MAX_ALT_LENGTH} characters")

        thumbnail = row.get("thumbnail_url", "")
        if thumbnail and not _canonical_public_url(thumbnail):
            errors.append(f"{prefix} has an unsafe or non-canonical thumbnail URL")

        if row.get("robots_allowed") is not True:
            errors.append(f"{prefix} is not explicitly robots-allowed")
        else:
            robots_allowed += 1
        if row.get("policy_status") != "robots_allowed_not_fetched":
            errors.append(f"{prefix} must remain marked as metadata-only and not fetched")
        else:
            not_fetched += 1

        if row.get("rights_status") != "unknown" or row.get("usage_rights") != "Unknown":
            errors.append(f"{prefix} must not claim unverified reuse rights")
        else:
            rights_unknown += 1

        first_seen = row.get("first_seen_at")
        last_seen = row.get("last_seen_at")
        seen_count = row.get("seen_count")
        if not _nonnegative_integer(first_seen) or first_seen == 0:
            errors.append(f"{prefix} first_seen_at must be a positive integer")
        if not _nonnegative_integer(last_seen) or last_seen == 0:
            errors.append(f"{prefix} last_seen_at must be a positive integer")
        if _nonnegative_integer(first_seen) and _nonnegative_integer(last_seen) and last_seen < first_seen:
            errors.append(f"{prefix} last_seen_at precedes first_seen_at")
        if (
            not isinstance(seen_count, int)
            or isinstance(seen_count, bool)
            or not 1 <= seen_count <= MAX_SEEN_COUNT
        ):
            errors.append(f"{prefix} seen_count is outside the allowed range")

    metrics = {
        "candidates": len(rows),
        "unique_candidates": len(seen),
        "media_hosts": len(origins),
        "by_type": dict(sorted(types.items())),
        "robots_allowed": robots_allowed,
        "metadata_only_not_fetched": not_fetched,
        "unknown_rights": rights_unknown,
    }
    return errors, metrics


def validate(path: Path = CATALOG) -> tuple[list[str], dict[str, Any]]:
    if not path.is_file():
        return ["data/media-discovery.json is missing"], {}
    if path.stat().st_size > MAX_CATALOG_BYTES:
        return ["media catalog exceeds the 20 MB safety limit"], {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"media catalog is unreadable or invalid JSON: {error}"], {}
    return validate_document(document)


def main() -> None:
    errors, metrics = validate()
    if errors:
        for error in errors[:100]:
            print(f"ERROR: {error}", file=sys.stderr)
        if len(errors) > 100:
            print(f"ERROR: {len(errors) - 100} additional validation errors omitted", file=sys.stderr)
        raise SystemExit(1)

    report = [
        "## Hurricane media catalog validation",
        "",
        f"- Metadata candidates: **{metrics['candidates']:,}**",
        f"- Unique candidates: **{metrics['unique_candidates']:,}**",
        f"- Media hosts represented: **{metrics['media_hosts']:,}**",
        f"- Robots-allowed candidates: **{metrics['robots_allowed']:,}**",
        f"- Metadata-only, not fetched: **{metrics['metadata_only_not_fetched']:,}**",
        f"- Rights still unknown: **{metrics['unknown_rights']:,}**",
        f"- By type: **{json.dumps(metrics['by_type'], sort_keys=True)}**",
        "",
        "No media bytes were downloaded and no reuse permissions were inferred.",
        "",
    ]
    rendered = "\n".join(report)
    print(rendered, end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(rendered)


if __name__ == "__main__":
    main()
