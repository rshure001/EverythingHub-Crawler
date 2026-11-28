#!/usr/bin/env python3
"""Idempotently stage metadata-only page/catalog relationships in eh_quad.

This importer cannot write eh_catalog_records or any public search index.
It requires an explicit --apply flag and a separately applied migration.
"""
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/page-catalog-membership-staging.json"
TARGET_SCHEMA = "eh_quad"


def target_schema(value=None):
    requested = value if value is not None else os.environ.get("EH_DB_SCHEMA", TARGET_SCHEMA)
    if requested != TARGET_SCHEMA:
        raise ValueError("This importer is locked to eh_quad; no other schema is permitted.")
    return TARGET_SCHEMA


def valid_https(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        p = urlsplit(value)
        _ = p.port
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password


def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_rows(payload):
    target_schema()
    if (not isinstance(payload, dict)
        or payload.get("artifact_type") != "page_catalog_membership_staging"
        or payload.get("production_import_allowed") is not False
        or payload.get("public_search_allowed") is not False):
        raise ValueError("refusing artifact that is not explicitly metadata-only")
    candidates, memberships = payload.get("candidates"), payload.get("memberships")
    if not isinstance(candidates, list) or not isinstance(memberships, list):
        raise ValueError("candidates and memberships must be arrays")
    candidate_rows = {}
    for c in candidates:
        if not isinstance(c, dict):
            raise ValueError("candidate must be an object")
        if (not c.get("candidate_id") or not c.get("title") or not valid_https(c.get("target_url"))
            or c.get("record_kind") != "article_link_metadata"
            or c.get("staging_status") != "metadata_only_not_a_verified_record"
            or c.get("rights_status") != "per_record_review_required"
            or c.get("verified") is not False or c.get("content_fetched") is not False):
            raise ValueError("candidate violates metadata-only or source-rights constraints")
        for field in ("source_urls", "source_hosts", "source_provenance"):
            if not isinstance(c.get(field), list):
                raise ValueError(f"{field} must be an array")
        if any(not valid_https(u) for u in c["source_urls"]):
            raise ValueError("candidate source URL must be HTTPS")
        if any(not isinstance(p, dict) or not valid_https(p.get("source_url")) for p in c["source_provenance"]):
            raise ValueError("provenance entries require HTTPS source_url")
        key = c["candidate_id"]
        row = (key, c["title"], c["target_url"], c["record_kind"],
               c["staging_status"], c["rights_status"], json.dumps(c["source_urls"]),
               json.dumps(c["source_hosts"]), json.dumps(c["source_provenance"]),
               parse_ts(c.get("discovered_at")), parse_ts(c.get("last_seen_at")))
        if key in candidate_rows and candidate_rows[key][2] != c["target_url"]:
            raise ValueError("candidate ID collision with conflicting target URL")
        candidate_rows[key] = row
    membership_rows = {}
    for m in memberships:
        if (not isinstance(m, dict) or not m.get("membership_id") or not m.get("candidate_id")
            or not m.get("catalog_id") or m.get("relationship") != "candidate_child_link"
            or m.get("membership_status") != "metadata_only_not_public"
            or m.get("rights_status") != "per_record_review_required"):
            raise ValueError("membership violates metadata-only constraints")
        if m["candidate_id"] not in candidate_rows:
            raise ValueError("membership references an absent candidate")
        row = (m["membership_id"], m["candidate_id"], m["catalog_id"],
               m["relationship"], m["membership_status"], m["rights_status"])
        prior = membership_rows.get(m["membership_id"])
        if prior and prior != row:
            raise ValueError("membership ID collision")
        membership_rows[m["membership_id"]] = row
    return list(candidate_rows.values()), list(membership_rows.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--apply", action="store_true", help="write metadata-only staging rows; otherwise validate only")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    candidates, memberships = build_rows(payload)
    summary = {"destination_schema": TARGET_SCHEMA, "candidate_rows": len(candidates),
               "membership_rows": len(memberships), "public_records_written": 0,
               "public_search_allowed": False, "applied": False}
    if args.apply:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise SystemExit("DATABASE_URL is required; no destination was modified.")
        try:
            import psycopg
        except ImportError as exc:
            raise SystemExit("Install psycopg[binary]>=3.2 before importing.") from exc
        with psycopg.connect(database_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                  insert into eh_quad.eh_page_catalog_candidates
                    (candidate_id,title,target_url,record_kind,staging_status,rights_status,
                     verified,content_fetched,source_urls,source_hosts,source_provenance,discovered_at,last_seen_at)
                  values (%s,%s,%s,%s,%s,%s,false,false,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                  on conflict (candidate_id) do update set
                    title=excluded.title, source_urls=excluded.source_urls,
                    source_hosts=excluded.source_hosts, source_provenance=excluded.source_provenance,
                    last_seen_at=greatest(eh_quad.eh_page_catalog_candidates.last_seen_at,excluded.last_seen_at),
                    updated_at=now()
                  where eh_quad.eh_page_catalog_candidates.target_url=excluded.target_url
                    and eh_quad.eh_page_catalog_candidates.verified=false
                    and eh_quad.eh_page_catalog_candidates.content_fetched=false
                """, candidates)
                cur.executemany("""
                  insert into eh_quad.eh_page_catalog_candidate_memberships
                    (membership_id,candidate_id,catalog_id,relationship,membership_status,rights_status)
                  values (%s,%s,%s,%s,%s,%s)
                  on conflict (membership_id) do nothing
                """, memberships)
            conn.commit()
        summary["applied"] = True
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    target_schema()
    main()
