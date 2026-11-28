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


def _merge_json_list(left, right):
    """Merge JSON-compatible list values without dropping prior provenance."""
    values = {}
    for item in (left if isinstance(left, list) else []) + (right if isinstance(right, list) else []):
        values[json.dumps(item, sort_keys=True, separators=(",", ":"))] = item
    return [values[key] for key in sorted(values)]


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
        key, title, target = c.get("candidate_id"), c.get("title"), c.get("target_url")
        if (not isinstance(key, str) or not key.strip()
            or not isinstance(title, str) or not title.strip() or not valid_https(target)
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
        if any(not isinstance(host, str) or not host.strip() for host in c["source_hosts"]):
            raise ValueError("source hosts must be non-empty strings")
        row = (key, title.strip(), target, c["record_kind"], c["staging_status"], c["rights_status"],
               json.dumps(sorted(set(c["source_urls"]))),
               json.dumps(sorted(set(c["source_hosts"]))),
               json.dumps(_merge_json_list([], c["source_provenance"]), sort_keys=True),
               parse_ts(c.get("discovered_at")), parse_ts(c.get("last_seen_at")))
        prior = candidate_rows.get(key)
        if prior:
            if prior[2] != target:
                raise ValueError("candidate ID collision with conflicting target URL")
            # Merge all source metadata for duplicate IDs instead of replacing earlier evidence.
            merged_urls = sorted(set(json.loads(prior[6])) | set(json.loads(row[6])))
            merged_hosts = sorted(set(json.loads(prior[7])) | set(json.loads(row[7])))
            merged_provenance = _merge_json_list(json.loads(prior[8]), json.loads(row[8]))
            first_seen = min((x for x in (prior[9], row[9]) if x is not None), default=None)
            last_seen = max((x for x in (prior[10], row[10]) if x is not None), default=None)
            row = (key, prior[1], target, row[3], row[4], row[5],
                   json.dumps(merged_urls), json.dumps(merged_hosts),
                   json.dumps(merged_provenance, sort_keys=True), first_seen, last_seen)
        candidate_rows[key] = row

    membership_rows = {}
    logical_memberships = {}
    for m in memberships:
        if (not isinstance(m, dict) or not isinstance(m.get("membership_id"), str) or not m["membership_id"].strip()
            or not isinstance(m.get("candidate_id"), str) or not m["candidate_id"].strip()
            or not isinstance(m.get("catalog_id"), str) or not m["catalog_id"].strip()
            or m.get("relationship") != "candidate_child_link"
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
        pair = (m["candidate_id"], m["catalog_id"])
        prior_id = logical_memberships.get(pair)
        if prior_id and prior_id != m["membership_id"]:
            raise ValueError("duplicate candidate/catalog pair has conflicting membership IDs")
        logical_memberships[pair] = m["membership_id"]
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
                for row in candidates:
                    cur.execute("""
                      insert into eh_quad.eh_page_catalog_candidates
                        (candidate_id,title,target_url,record_kind,staging_status,rights_status,
                         verified,content_fetched,source_urls,source_hosts,source_provenance,discovered_at,last_seen_at)
                      values (%s,%s,%s,%s,%s,%s,false,false,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                      on conflict (candidate_id) do update set
                        source_urls = (
                          select coalesce(jsonb_agg(value order by value), '[]'::jsonb)
                          from (select distinct value from jsonb_array_elements(
                            eh_quad.eh_page_catalog_candidates.source_urls || excluded.source_urls)) merged
                        ),
                        source_hosts = (
                          select coalesce(jsonb_agg(value order by value), '[]'::jsonb)
                          from (select distinct value from jsonb_array_elements(
                            eh_quad.eh_page_catalog_candidates.source_hosts || excluded.source_hosts)) merged
                        ),
                        source_provenance = (
                          select coalesce(jsonb_agg(value order by value::text), '[]'::jsonb)
                          from (select distinct value from jsonb_array_elements(
                            eh_quad.eh_page_catalog_candidates.source_provenance || excluded.source_provenance)) merged
                        ),
                        discovered_at = case
                          when eh_quad.eh_page_catalog_candidates.discovered_at is null then excluded.discovered_at
                          when excluded.discovered_at is null then eh_quad.eh_page_catalog_candidates.discovered_at
                          else least(eh_quad.eh_page_catalog_candidates.discovered_at, excluded.discovered_at) end,
                        last_seen_at = case
                          when eh_quad.eh_page_catalog_candidates.last_seen_at is null then excluded.last_seen_at
                          when excluded.last_seen_at is null then eh_quad.eh_page_catalog_candidates.last_seen_at
                          else greatest(eh_quad.eh_page_catalog_candidates.last_seen_at, excluded.last_seen_at) end,
                        updated_at=now()
                      where eh_quad.eh_page_catalog_candidates.target_url=excluded.target_url
                        and eh_quad.eh_page_catalog_candidates.verified=false
                        and eh_quad.eh_page_catalog_candidates.content_fetched=false
                    """, row)
                    if cur.rowcount != 1:
                        raise ValueError("candidate ID already exists with a conflicting URL or verified/fetched state")
                for row in memberships:
                    cur.execute("""
                      select membership_id from eh_quad.eh_page_catalog_candidate_memberships
                      where candidate_id=%s and catalog_id=%s
                    """, (row[1], row[2]))
                    existing = cur.fetchone()
                    if existing and existing[0] != row[0]:
                        raise ValueError("database already has this candidate/catalog pair under a different membership ID")
                    cur.execute("""
                      insert into eh_quad.eh_page_catalog_candidate_memberships
                        (membership_id,candidate_id,catalog_id,relationship,membership_status,rights_status)
                      values (%s,%s,%s,%s,%s,%s)
                      on conflict (membership_id) do update set
                        relationship=excluded.relationship,
                        membership_status=excluded.membership_status,
                        rights_status=excluded.rights_status
                      where eh_quad.eh_page_catalog_candidate_memberships.candidate_id=excluded.candidate_id
                        and eh_quad.eh_page_catalog_candidate_memberships.catalog_id=excluded.catalog_id
                    """, row)
                    if cur.rowcount != 1:
                        raise ValueError("membership ID already exists for a different candidate/catalog pair")
            conn.commit()
        summary["applied"] = True
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    target_schema()
    main()
