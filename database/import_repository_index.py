#!/usr/bin/env python3
"""Idempotently import the repository crawler index into eh_quad only.

Required environment:
  DATABASE_URL=postgresql://... (store as a secret; never commit credentials)
Optional guard:
  EH_DB_SCHEMA=eh_quad (any other value is rejected)

The import is additive/upsert-only. It never deletes destination records.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crawler"))
from lane_classifier import VALID_LANES, classify_lane
from index_store import load_index

INDEX = ROOT / "data" / "index.json"
TARGET_SCHEMA = "eh_quad"


def iso_timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def target_schema(value=None):
    requested = value if value is not None else os.environ.get("EH_DB_SCHEMA", TARGET_SCHEMA)
    if requested != TARGET_SCHEMA:
        raise ValueError("This importer is locked to eh_quad; no other schema is permitted.")
    return TARGET_SCHEMA


def is_robots_allowed(page):
    """Treat an explicit robots denial as non-importable; legacy records default to allowed."""
    return page.get("robots_allowed") is not False


def is_public_http_url(value):
    """Accept absolute HTTP(S) URLs with a valid authority and no embedded credentials."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing .port validates malformed and out-of-range port values.
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in ("http", "https")
        and bool(hostname)
        and not any(character.isspace() for character in hostname)
        and parsed.username is None
        and parsed.password is None
    )


def collect_robots_denied_urls(pages):
    """Return unique public URLs explicitly denied by the current robots policy."""
    return sorted({
        page["url"]
        for page in pages
        if isinstance(page, dict)
        and page.get("robots_allowed") is False
        and is_public_http_url(page.get("url"))
    })


def page_lane(page, url):
    """Preserve a valid crawler lane or classify the source host safely."""
    lane = page.get("lane")
    if isinstance(lane, str) and lane in VALID_LANES:
        return lane
    return classify_lane(url)


def build_upsert_statement():
    schema = target_schema()
    return f"""
      insert into "{schema}".eh_documents as target
        (canonical_url, source_host, lane, title, description, body_text,
         language_code, http_status, content_type, content_hash, fetched_at,
         published_at, robots_allowed)
      values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      on conflict (canonical_url) do update set
        source_host = excluded.source_host,
        lane = excluded.lane,
        title = excluded.title,
        description = excluded.description,
        body_text = excluded.body_text,
        content_hash = excluded.content_hash,
        fetched_at = greatest(target.fetched_at, excluded.fetched_at),
        robots_allowed = excluded.robots_allowed,
        updated_at = now()
      where target.content_hash is distinct from excluded.content_hash
         or target.lane is distinct from excluded.lane
         or target.robots_allowed is distinct from excluded.robots_allowed
    """


def build_robots_exclusion_statement():
    """Soft-disable exact existing rows denied by current robots policy; never delete."""
    schema = target_schema()
    return f"""
      update "{schema}".eh_documents
      set robots_allowed = false, updated_at = now()
      where canonical_url = any(%s)
        and robots_allowed is distinct from false
    """


def main():
    target_schema()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required; no destination was modified.")
    if not INDEX.exists():
        raise SystemExit(f"Index file not found: {INDEX}")

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Install psycopg[binary]>=3.2 before importing.") from exc

    payload = load_index(INDEX)
    pages = payload.get("pages", [])
    rows = []
    skipped = 0
    denied_urls = collect_robots_denied_urls(pages)
    denied_url_set = set(denied_urls)

    for page in pages:
        if not isinstance(page, dict):
            skipped += 1
            continue
        url = page.get("url")
        if not is_public_http_url(url):
            skipped += 1
            continue
        # If duplicate source records disagree, an explicit denial wins.
        if url in denied_url_set or not is_robots_allowed(page):
            skipped += 1
            continue
        parsed = urlsplit(url)

        title = str(page.get("title") or "")[:2000]
        description = str(page.get("description") or "")[:5000]
        body = str(page.get("text") or "")[:200000]
        fetched = iso_timestamp(page.get("fetched_at")) or datetime.now(timezone.utc)
        digest = hashlib.sha256(
            (title + "\n" + description + "\n" + body).encode("utf-8")
        ).hexdigest()

        rows.append((
            url, parsed.hostname.lower(), page_lane(page, url), title, description, body,
            None, None, None, digest, fetched, None, True
        ))

    if not rows and not denied_urls:
        print(f"No valid records to import; skipped {skipped}.")
        return

    statement = build_upsert_statement()
    exclusion_statement = build_robots_exclusion_statement()
    processed = 0
    robots_excluded = 0
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), 250):
                batch = rows[offset:offset + 250]
                cursor.executemany(statement, batch)
                processed += len(batch)
                connection.commit()
                print(f"Processed {processed:,}/{len(rows):,}")

            if denied_urls:
                cursor.execute(exclusion_statement, (denied_urls,))
                robots_excluded = max(0, cursor.rowcount)
                connection.commit()

    print(
        f"Import complete. Processed {processed:,}; skipped {skipped:,}; "
        f"soft-excluded {robots_excluded:,} existing robots-denied documents."
    )
    print("Rows processed are not the same as newly inserted rows; no destination rows are deleted.")


if __name__ == "__main__":
    main()
