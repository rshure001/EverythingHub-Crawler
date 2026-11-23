#!/usr/bin/env python3
"""Adapt discovered CKAN datasets into the shared universal catalog contract.

Output is a normalized, deduplicated staging artifact. It is not advertised as
production-indexed until a separate database/search ingestion step succeeds.
"""
from __future__ import annotations
import argparse, gzip, json, hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from catalog_model import SCHEMA_VERSION, catalog_metrics, normalize_catalog, normalize_record
from catalog_source_policy import policy_for_host

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "catalogs.json.gz"
OUTPUT = ROOT / "data" / "universal-catalog-records.json.gz"
OPENLIBRARY_INPUT = ROOT / "data" / "openlibrary-catalogs.json.gz"

MAX_TAGS_PER_DATASET = 10
MIN_ORGANIZATION_SUBCATALOG_MEMBERS = 2
MIN_TAG_SUBCATALOG_MEMBERS = 3
MAX_ORGANIZATION_SUBCATALOGS = 200
MAX_TAG_SUBCATALOGS = 400


def _group_text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _dataset_key(dataset: dict[str, Any]) -> str:
    return _group_text(dataset.get("dataset_id") or dataset.get("id"), 300)


def _build_subcatalogs(
    source_id: str,
    parent_catalog: dict[str, Any],
    datasets: list[dict[str, Any]],
    source_policy: dict[str, Any],
    updated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    """Create bounded, evidence-backed organization/tag subcatalogs for CKAN metadata."""
    if parent_catalog.get("catalog_type") != "open_data":
        return [], {}

    groups: dict[str, dict[str, set[str]]] = {
        "organization": defaultdict(set),
        "tag": defaultdict(set),
    }
    labels: dict[tuple[str, str], str] = {}
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        dataset_key = _dataset_key(dataset)
        if not dataset_key:
            continue
        organization = _group_text(dataset.get("organization"), 300)
        if organization:
            key = organization.casefold()
            groups["organization"][key].add(dataset_key)
            label_key = ("organization", key)
            current_label = labels.get(label_key)
            if current_label is None or (organization.casefold(), organization) < (current_label.casefold(), current_label):
                labels[label_key] = organization

        raw_tags = dataset.get("tags") or []
        if not isinstance(raw_tags, list):
            continue
        normalized_tags = set()
        for raw_tag in raw_tags:
            if isinstance(raw_tag, dict):
                raw_tag = raw_tag.get("name")
            label = _group_text(raw_tag, 80)
            if label:
                normalized_tags.add(label)
        for label in sorted(normalized_tags, key=lambda value: (value.casefold(), value))[:MAX_TAGS_PER_DATASET]:
            key = label.casefold()
            groups["tag"][key].add(dataset_key)
            label_key = ("tag", key)
            current_label = labels.get(label_key)
            if current_label is None or (label.casefold(), label) < (current_label.casefold(), current_label):
                labels[label_key] = label

    candidates: list[tuple[str, str, str, list[str]]] = []
    for kind, minimum, maximum in (
        ("organization", MIN_ORGANIZATION_SUBCATALOG_MEMBERS, MAX_ORGANIZATION_SUBCATALOGS),
        ("tag", MIN_TAG_SUBCATALOG_MEMBERS, MAX_TAG_SUBCATALOGS),
    ):
        rows = [
            (kind, key, labels[(kind, key)], sorted(members))
            for key, members in groups[kind].items()
            if len(members) >= minimum
        ]
        rows.sort(key=lambda row: (-len(row[3]), row[2].casefold(), row[1]))
        candidates.extend(rows[:maximum])

    subcatalogs: list[dict[str, Any]] = []
    memberships: dict[str, set[str]] = defaultdict(set)
    for kind, key, label, member_keys in candidates:
        digest = hashlib.sha256((kind + "\\0" + key).encode("utf-8")).hexdigest()[:16]
        subcatalog = normalize_catalog({
            "source_id": f"{source_id}:{kind}:{digest}",
            "title": f"{parent_catalog['title']} — {label}",
            "description": (
                f"Dataset subcategory grouped by {kind} metadata. "
                "Individual dataset rights and attribution remain per record."
            ),
            "catalog_type": parent_catalog["catalog_type"],
            "subject": label,
            "publisher": parent_catalog["publisher"],
            "source_url": parent_catalog["source_url"],
            "access_method": parent_catalog["access_method"],
            "coverage_status": parent_catalog["coverage_status"],
            "record_count": len(member_keys),
            "last_discovered_at": updated_at,
            "catalog_level": "subcatalog",
            "parent_catalog_id": parent_catalog["catalog_id"],
            "subcategory_kind": kind,
            "subcategory_value": label,
        })
        subcatalog.update({
            "source_terms_url": source_policy.get("terms_url", ""),
            "source_terms_status": source_policy.get("terms_status", "unreviewed"),
            "source_terms_reviewed_at": source_policy.get("reviewed_at", ""),
            "attribution_required": source_policy.get("attribution_required", "per_dataset"),
            "metadata_only": True,
            "record_level_clearance": "not_reviewed",
            "public_search_allowed": False,
        })
        subcatalogs.append(subcatalog)
        for dataset_key in member_keys:
            memberships[dataset_key].add(subcatalog["catalog_id"])
    return subcatalogs, memberships


def adapt(manifest: dict[str, Any]) -> dict[str, Any]:
    catalogs, source_pages = [], []
    records_by_id: dict[str, dict[str, Any]] = {}
    for source in manifest.get("catalogs", []):
        source_id = source.get("id")
        if not source_id:
            continue
        base = source.get("base_url")
        if not isinstance(base, str) or not base.startswith("https://"):
            continue
        datasets = [x for x in manifest.get("datasets", []) if x.get("catalog_id") == source_id]
        errors = source.get("errors") or []
        catalog_type = source.get("catalog_type") or "open_data"
        source_host = urlsplit(base).hostname or ""
        source_policy = policy_for_host(source_host) or {}
        # The checked-in source policy is authoritative. Discovery manifests can
        # be older than the policy registry, so never trust their terms flags.
        source_terms_status = source_policy.get("terms_status", "unreviewed")
        source_terms_url = source_policy.get("terms_url") or ""
        source_terms_reviewed_at = source_policy.get("reviewed_at") or ""
        catalog_policy_approved = (
            source_policy.get("discovery_allowed") is True
            and source_terms_status in ("reviewed", "reviewed_per_dataset")
            and bool(source_terms_reviewed_at)
            and catalog_type != "library_archive"
        )
        catalog = normalize_catalog({
            "source_id": source_id,
            "title": source.get("name") or source_id,
            "description": source.get("description") or "Public collection discovered through a machine-readable catalog interface.",
            "catalog_type": catalog_type,
            "subject": source.get("category") or "open data",
            "publisher": source.get("name") or "",
            "source_url": source.get("source_url") or base.rstrip("/") + source.get("api_path", "/api/3/action/package_search"),
            "access_method": source.get("access_method") or "api",
            "coverage_status": source.get("coverage_status") or ("partial" if errors or datasets else "unknown"),
            "record_count": len(datasets),
            "last_discovered_at": manifest.get("updated_at") or "",
        })
        catalog["source_terms_url"] = source_terms_url
        catalog["source_terms_status"] = source_terms_status
        catalog["source_terms_reviewed_at"] = source_terms_reviewed_at
        catalog["attribution_required"] = source_policy.get(
            "attribution_required", source.get("attribution_required", "per_dataset")
        )
        catalog["public_search_allowed"] = catalog_policy_approved
        catalogs.append(catalog)
        subcatalogs, subcatalog_memberships = (
            _build_subcatalogs(
                source_id, catalog, datasets, source_policy, manifest.get("updated_at") or ""
            )
            if catalog_policy_approved else ([], {})
        )
        catalogs.extend(subcatalogs)
        for dataset in datasets:
            landing = dataset.get("landing_url")
            if not isinstance(landing, str):
                continue
            if not landing.startswith("https://"):
                raise ValueError("dataset landing URLs must use HTTPS")
            license_info = dataset.get("license") or {}
            if not isinstance(license_info, dict):
                license_info = {}
            license_present = bool(license_info.get("url") or license_info.get("id"))
            resource_rights_present = any(
                isinstance(resource, dict) and bool(str(resource.get("rights") or "").strip())
                for resource in (dataset.get("resources") or [])
            )
            rights_metadata_present = bool(
                license_present
                or license_info.get("rights")
                or license_info.get("is_open") is not None
                or resource_rights_present
            )
            private_dataset = license_info.get("private") is True
            public_search_allowed = catalog_policy_approved and rights_metadata_present and not private_dataset
            attributes = dataset.get("attributes") or {
                "organization": dataset.get("organization") or "",
                "tags": dataset.get("tags") or [],
                "resources": dataset.get("resources") or [],
                "resource_count": int(dataset.get("resource_count") or 0),
                "metadata_modified": dataset.get("metadata_modified") or "",
            }
            if not isinstance(attributes, dict):
                attributes = {}
            attributes = dict(attributes)
            attributes.update({
                "license": license_info,
                "source_terms_url": dataset.get("source_terms_url") or source_terms_url,
                "source_terms_status": source_terms_status,
                "source_terms_reviewed_at": source_terms_reviewed_at,
                "attribution_required": dataset.get("attribution_required", catalog.get("attribution_required")),
                "rights_status": (
                    "private_dataset" if private_dataset
                    else "license_metadata_present" if license_present
                    else "dataset_rights_metadata_present" if rights_metadata_present
                    else "review_required"
                ),
                "public_search_allowed": public_search_allowed,
            })
            raw = {
                "source_key": dataset.get("dataset_id") or dataset.get("id"),
                "record_type": dataset.get("record_type") or "dataset",
                "title": dataset.get("title") or dataset.get("dataset_id"),
                "description": dataset.get("description") or "",
                "source_url": landing,
                "canonical_entity_key": dataset.get("canonical_entity_key") or ("dataset:" + str(dataset.get("id") or dataset.get("dataset_id"))),
                "subject": dataset.get("subject") or ", ".join(dataset.get("tags") or [])[:200],
                "attributes": attributes,
                "last_seen_at": manifest.get("updated_at") or "",
            }
            record = normalize_record(raw, catalog["catalog_id"])
            record["attributes"] = attributes
            dataset_key = _dataset_key(dataset)
            record["catalog_ids"] = sorted({
                catalog["catalog_id"],
                *subcatalog_memberships.get(dataset_key, set()),
            })
            record["source_urls"] = sorted(set(record.get("source_urls") or [landing]) | {landing})
            provenance = {
                "catalog_id": catalog["catalog_id"],
                "source_url": landing,
                "source_terms_url": source_terms_url,
                "source_terms_status": source_terms_status,
                "source_terms_reviewed_at": source_terms_reviewed_at,
                "attribution_required": dataset.get(
                    "attribution_required", catalog.get("attribution_required")
                ),
                "public_search_allowed": public_search_allowed,
                "rights_status": attributes.get("rights_status", "review_required"),
            }
            record["source_provenance"] = [provenance]
            existing = records_by_id.get(record["record_id"])
            if existing:
                existing_catalog_ids = existing.get("catalog_ids") or (
                    [existing["catalog_id"]] if existing.get("catalog_id") else []
                )
                existing["catalog_ids"] = sorted(set(existing_catalog_ids) | set(record["catalog_ids"]))
                existing_source_urls = existing.get("source_urls") or (
                    [existing["source_url"]] if existing.get("source_url") else []
                )
                existing["source_urls"] = sorted(set(existing_source_urls) | {landing})
                provenance_rows = existing.get("source_provenance") or []
                provenance_key = (provenance["catalog_id"], provenance["source_url"])
                if not any(
                    isinstance(row, dict)
                    and (row.get("catalog_id"), row.get("source_url")) == provenance_key
                    for row in provenance_rows
                ):
                    provenance_rows.append(provenance)
                existing["source_provenance"] = provenance_rows
                existing_attributes = existing.get("attributes") or {}
                for attribute_key, attribute_value in attributes.items():
                    if (
                        attribute_key not in existing_attributes
                        or existing_attributes[attribute_key] in (None, "", [], {})
                    ):
                        existing_attributes[attribute_key] = attribute_value
                existing_attributes["public_search_allowed"] = (
                    bool(existing_attributes.get("public_search_allowed"))
                    and bool(public_search_allowed)
                )
                existing["attributes"] = existing_attributes
                existing["last_seen_at"] = max(
                    str(existing.get("last_seen_at") or ""),
                    str(record.get("last_seen_at") or ""),
                )
            else:
                records_by_id[record["record_id"]] = record
            source_pages.append({
                "url": landing,
                "canonical_url": landing,
                "fetched": False,
                "policy_allowed": public_search_allowed,
                "source_terms_status": source_terms_status,
                "record_id": record["record_id"],
                "catalog_id": catalog["catalog_id"],
                "catalog_ids": record["catalog_ids"],
            })
    records = [records_by_id[key] for key in sorted(records_by_id)]
    catalog_ids = {catalog["catalog_id"] for catalog in catalogs}
    for catalog in catalogs:
        parent_catalog_id = catalog.get("parent_catalog_id") or ""
        if parent_catalog_id and parent_catalog_id not in catalog_ids:
            raise ValueError("subcatalog parent is missing from the catalog registry")
    membership_counts = Counter(
        catalog_id
        for record in records
        for catalog_id in (record.get("catalog_ids") or [record.get("catalog_id")])
        if catalog_id
    )
    for catalog in catalogs:
        catalog["record_count"] = membership_counts.get(catalog["catalog_id"], 0)
    metrics = catalog_metrics(catalogs, records, source_pages)
    metrics["production_eligible_records"] = sum(
        1 for record in records if (record.get("attributes") or {}).get("public_search_allowed") is True
    )
    metrics["restricted_records"] = len(records) - metrics["production_eligible_records"]
    metrics["approved_source_page_references"] = len({
        page.get("canonical_url") or page.get("url")
        for page in source_pages
        if page.get("policy_allowed") is True and (page.get("canonical_url") or page.get("url"))
    })
    metrics["source_provenance_associations"] = sum(
        len(record.get("source_provenance") or []) for record in records
    )
    metrics["unique_records_by_type"] = dict(sorted(Counter(record["record_type"] for record in records).items()))
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": "universal-catalog-staging",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "production_indexed": False,
        "metrics": metrics,
        "catalogs": catalogs,
        "records": records,
        "source_pages": source_pages,
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    raw = gzip.decompress(args.input.read_bytes()) if args.input.suffix == ".gz" else args.input.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    if OPENLIBRARY_INPUT.exists():
        extra_raw = gzip.decompress(OPENLIBRARY_INPUT.read_bytes())
        extra = json.loads(extra_raw.decode("utf-8"))
        manifest = {
            **manifest,
            "catalogs": manifest.get("catalogs", []) + extra.get("catalogs", []),
            "datasets": manifest.get("datasets", []) + extra.get("datasets", []),
        }
    result = adapt(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.output.suffix == ".gz":
        temp.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temp.write_bytes(payload)
    temp.replace(args.output)
    print(json.dumps(result["metrics"], indent=2))

if __name__ == "__main__":
    main()
