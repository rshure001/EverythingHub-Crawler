#!/usr/bin/env python3
"""Build an additive, non-public catalog-membership staging artifact.

This is a bridge from the canonical metadata-only expansion ledger. It does
not create universal public records, authorize content fetches, write SQL, or
publish search indexes.
"""
from __future__ import annotations
import argparse, gzip, hashlib, json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/page-catalog-expansion.json.gz"
DEFAULT_OUTPUT = ROOT / "data/page-catalog-membership-staging.json"
SCHEMA_VERSION = 1

def _https(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        p = urlsplit(value)
        _ = p.port
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password

def _stable(prefix, *parts):
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()

def build_membership_staging(ledger):
    if not isinstance(ledger, dict) or not isinstance(ledger.get("records"), list):
        raise ValueError("expansion ledger must contain a records array")
    by_record = {}
    memberships = {}
    rejected = 0
    source_record_count = len(ledger["records"])
    page_child_input_count = sum(1 for row in ledger["records"] if isinstance(row, dict) and isinstance(row.get("record_id"), str) and row["record_id"].startswith("page-child:"))
    for row in ledger["records"]:
        if not isinstance(row, dict):
            rejected += 1; continue
        rid, title = row.get("record_id"), row.get("title")
        target = row.get("article_url")
        catalog_ids = row.get("catalog_ids")
        if (not isinstance(rid, str) or not rid.startswith("page-child:")
            or not isinstance(title, str) or not title.strip()
            or not _https(target) or not isinstance(catalog_ids, list)
            or not catalog_ids or any(not isinstance(c, str) or not c.strip() for c in catalog_ids)
            or row.get("verified") is not False
            or row.get("content_fetched") is not False
            or row.get("rights_status") != "per_record_review_required"
            or row.get("record_type") != "article_link_metadata"):
            rejected += 1; continue
        prior = by_record.get(rid)
        if prior and prior["target_url"] != target:
            raise ValueError("record ID collision with conflicting target URL")
        if not prior:
            prior = {
                "candidate_id": rid, "title": title.strip(), "target_url": target,
                "record_kind": "article_link_metadata",
                "staging_status": "metadata_only_not_a_verified_record",
                "rights_status": "per_record_review_required",
                "verified": False, "content_fetched": False,
                "source_urls": [], "source_hosts": [], "source_provenance": [],
                "discovered_at": row.get("discovered_at"),
                "last_seen_at": row.get("last_seen_at"),
            }
            by_record[rid] = prior
        for field in ("source_urls", "source_hosts"):
            vals = row.get(field, [])
            if isinstance(vals, list):
                prior[field] = sorted(set(prior[field]) | {v for v in vals if isinstance(v, str) and v})
        prov = row.get("source_provenance", [])
        if isinstance(prov, list):
            known = {json.dumps(p, sort_keys=True) for p in prior["source_provenance"]}
            for p in prov:
                if isinstance(p, dict) and _https(p.get("source_url")):
                    key = json.dumps(p, sort_keys=True)
                    if key not in known:
                        prior["source_provenance"].append(p); known.add(key)
        for cid in sorted(set(catalog_ids)):
            mid = _stable("membership:", rid, cid)
            membership = {
                "membership_id": mid, "candidate_id": rid, "catalog_id": cid,
                "relationship": "candidate_child_link",
                "membership_status": "metadata_only_not_public",
                "rights_status": "per_record_review_required",
            }
            existing = memberships.get(mid)
            if existing and existing != membership:
                raise ValueError("membership ID collision")
            memberships[mid] = membership
    candidates = sorted(by_record.values(), key=lambda x: x["candidate_id"])
    links = sorted(memberships.values(), key=lambda x: (x["catalog_id"], x["candidate_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "page_catalog_membership_staging",
        "production_import_allowed": False,
        "public_search_allowed": False,
        "source_ledger": "data/page-catalog-expansion.json.gz",
        "source_record_count": source_record_count,
        "page_child_input_count": page_child_input_count,
        "candidate_count": len(candidates),
        "membership_count": len(links),
        "rejected_rows": rejected,
        "candidates": candidates,
        "memberships": links,
    }

def read_json_or_gzip(path):
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_membership_staging(read_json_or_gzip(args.input))
    if result["candidate_count"] == 0 or result["membership_count"] == 0:
        raise SystemExit("Refusing to replace the staging artifact with an empty bridge: " + json.dumps({k: result[k] for k in ("source_record_count", "page_child_input_count", "candidate_count", "membership_count", "rejected_rows")}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps({k: result[k] for k in ("artifact_type","source_record_count","page_child_input_count","candidate_count","membership_count","rejected_rows","production_import_allowed","public_search_allowed")}, indent=2))

if __name__ == "__main__":
    main()
