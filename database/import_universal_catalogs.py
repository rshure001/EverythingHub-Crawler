#!/usr/bin/env python3
"""Additively import staged universal catalogs and records into eh_quad only."""
from __future__ import annotations

import gzip
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "universal-catalog-records.json.gz"
TARGET_SCHEMA = "eh_quad"


def target_schema(value=None):
    requested = value if value is not None else os.environ.get("EH_DB_SCHEMA", TARGET_SCHEMA)
    if requested != TARGET_SCHEMA:
        raise ValueError("This importer is locked to eh_quad; no other schema is permitted.")
    return TARGET_SCHEMA


def valid_https_url(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_catalog_rows(payload):
    eligible_counts = Counter(
        item.get("catalog_id") for item in payload.get("records", [])
        if isinstance(item, dict) and (item.get("attributes") or {}).get("public_search_allowed") is True
    )
    rows = []
    for item in payload.get("catalogs", []):
        if not isinstance(item, dict) or not valid_https_url(item.get("source_url")):
            raise ValueError("every catalog must have a valid HTTPS source_url")
        if not item.get("catalog_id") or not item.get("source_id") or not item.get("title"):
            raise ValueError("catalog_id, source_id and title are required")
        rows.append((
            item["catalog_id"], item["source_id"], item["title"],
            item.get("description") or "", item["catalog_type"],
            item.get("subject") or "", item.get("publisher") or "",
            item.get("geography") or "", item["source_url"],
            item.get("access_method") or "web",
            item.get("coverage_status") or "unknown",
            max(0, int(eligible_counts.get(item["catalog_id"], 0))),
            parse_timestamp(item.get("last_discovered_at")),
            parse_timestamp(item.get("last_synced_at")),
        ))
    return rows


def build_record_rows(payload):
    rows = []
    for item in payload.get("records", []):
        if not isinstance(item, dict) or not valid_https_url(item.get("source_url")):
            raise ValueError("every catalog record must have a valid HTTPS source_url")
        if not all(item.get(key) for key in ("record_id", "catalog_id", "record_type", "title", "source_key")):
            raise ValueError("record_id, catalog_id, record_type, title and source_key are required")
        attrs = item.get("attributes") or {}
        if not isinstance(attrs, dict):
            raise ValueError("record attributes must be a JSON object")
        if attrs.get("public_search_allowed") is not True:
            continue
        rows.append((
            item["record_id"], item["catalog_id"], item["record_type"],
            item["title"], item.get("description") or "", item["source_key"],
            item["source_url"], item.get("canonical_entity_key") or "",
            item.get("subject") or "", item.get("geography") or "",
            json.dumps(attrs, ensure_ascii=False),
            parse_timestamp(item.get("last_seen_at")),
        ))
    return rows


def catalog_upsert_sql():
    target_schema()
    return """
      insert into eh_quad.eh_catalogs as target
        (catalog_id, source_id, title, description, catalog_type, subject,
         publisher, geography, source_url, access_method, coverage_status,
         record_count, last_discovered_at, last_synced_at)
      values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      on conflict (catalog_id) do update set
        title=excluded.title, description=excluded.description,
        catalog_type=excluded.catalog_type, subject=excluded.subject,
        publisher=excluded.publisher, geography=excluded.geography,
        source_url=excluded.source_url, access_method=excluded.access_method,
        coverage_status=excluded.coverage_status,
        record_count=excluded.record_count,
        last_discovered_at=greatest(target.last_discovered_at, excluded.last_discovered_at),
        last_synced_at=excluded.last_synced_at, updated_at=now()
    """


def record_upsert_sql():
    target_schema()
    return """
      insert into eh_quad.eh_catalog_records as target
        (record_id, catalog_id, record_type, title, description, source_key,
         source_url, canonical_entity_key, subject, geography, attributes, last_seen_at)
      values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
      on conflict (record_id) do update set
        catalog_id=excluded.catalog_id, record_type=excluded.record_type,
        title=excluded.title, description=excluded.description,
        source_key=excluded.source_key, source_url=excluded.source_url,
        canonical_entity_key=excluded.canonical_entity_key,
        subject=excluded.subject, geography=excluded.geography,
        attributes=excluded.attributes,
        last_seen_at=greatest(target.last_seen_at, excluded.last_seen_at),
        updated_at=now()
    """


def main():
    target_schema()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required; no destination was modified.")
    raw = gzip.decompress(INPUT.read_bytes()) if INPUT.suffix == ".gz" else INPUT.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    catalogs = build_catalog_rows(payload)
    records = build_record_rows(payload)
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Install psycopg[binary]>=3.2 before importing.") from exc
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(catalog_upsert_sql(), catalogs)
            connection.commit()
            for offset in range(0, len(records), 500):
                cursor.executemany(record_upsert_sql(), records[offset:offset + 500])
                connection.commit()
                print(f"Catalog records processed: {min(offset + 500, len(records)):,}/{len(records):,}")
    print(json.dumps({
        "catalogs_processed": len(catalogs),
        "catalog_records_processed": len(records),
        "production_indexed": False,
        "destination_schema": TARGET_SCHEMA,
    }, indent=2))


if __name__ == "__main__":
    main()
