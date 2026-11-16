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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
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
        title = excluded.title,
        description = excluded.description,
        body_text = excluded.body_text,
        content_hash = excluded.content_hash,
        fetched_at = greatest(target.fetched_at, excluded.fetched_at),
        updated_at = now()
      where target.content_hash is distinct from excluded.content_hash
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

    payload = json.loads(INDEX.read_text(encoding="utf-8"))
    pages = payload.get("pages", [])
    rows = []
    skipped = 0

    for page in pages:
        if not isinstance(page, dict):
            skipped += 1
            continue
        url = page.get("url")
        if not isinstance(url, str):
            skipped += 1
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            skipped += 1
            continue

        title = str(page.get("title") or "")[:2000]
        description = str(page.get("description") or "")[:5000]
        body = str(page.get("text") or "")[:200000]
        fetched = iso_timestamp(page.get("fetched_at")) or datetime.now(timezone.utc)
        digest = hashlib.sha256(
            (title + "\n" + description + "\n" + body).encode("utf-8")
        ).hexdigest()

        rows.append((
            url, parsed.hostname.lower(), "general", title, description, body,
            None, None, None, digest, fetched, None, True
        ))

    if not rows:
        print(f"No valid records to import; skipped {skipped}.")
        return

    statement = build_upsert_statement()
    processed = 0
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), 250):
                batch = rows[offset:offset + 250]
                cursor.executemany(statement, batch)
                processed += len(batch)
                connection.commit()
                print(f"Processed {processed:,}/{len(rows):,}")

    print(f"Import complete. Processed {processed:,}; skipped {skipped:,}.")
    print("Rows processed are not the same as newly inserted rows.")


if __name__ == "__main__":
    main()
