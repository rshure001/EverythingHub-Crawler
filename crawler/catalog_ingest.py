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


def _dataset_rights(dataset: dict[str, Any], catalog_policy_approved: bool) -> dict[str, Any]:
    """Evaluate dataset-level reuse eligibility without trusting upstream open flags alone."""
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
    rights_values = [
        license_info.get("rights"), license_info.get("title"),
        license_info.get("id"), license_info.get("url"),
    ]
    rights_values.extend(
        resource.get("rights")
        for resource in (dataset.get("resources") or [])
        if isinstance(resource, dict)
    )
    rights_text = " ".join(str(value or "").casefold() for value in rights_values)
    normalized_rights_text = rights_text.replace("-", " ").replace("_", " ")
    explicit_restriction = any(marker in normalized_rights_text for marker in (
        "noncommercial", "non commercial", "no derivatives",
        "cc by nc", "cc by nd", "cc by sa nc",
        "all rights reserved", "no redistribution", "not for redistribution",
    ))
    explicit_open_rights = (
        any(marker in rights_text for marker in (
            "open use", "public domain", "cc0", "cc-by",
            "creative commons attribution",
        ))
        or any(marker in normalized_rights_text for marker in (
            "open government licence", "open government license", "cc by",
            "creative commons attribution",
        ))
    ) and not explicit_restriction
    # Explicit restrictive terms override an inconsistent upstream is_open flag.
    open_rights_verified = (
        license_info.get("is_open") is True or explicit_open_rights
    ) and not explicit_restriction
    closed_license = explicit_restriction or (
        license_info.get("is_open") is False and not explicit_open_rights
    )
    public_search_allowed = (
        catalog_policy_approved
        and rights_metadata_present
        and open_rights_verified
        and not private_dataset
        and not closed_license
    )
    rights_status = (
        "private_dataset" if private_dataset
        else "closed_license" if closed_license
        else "license_metadata_present" if license_present
        else "dataset_rights_metadata_present" if rights_metadata_present
        else "review_required"
    )
    return {
        "license": license_info,
        "rights_metadata_present": rights_metadata_present,
        "private_dataset": private_dataset,
        "closed_license": closed_license,
        "public_search_allowed": public_search_allowed,
        "rights_status": rights_status,
    }


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
            # Subcatalogs are derived only from dataset rows that passed the
            # same per-record rights check used by the public index builder.
            "metadata_only": False,
            "record_level_clearance": "eligible_per_record",
            "public_search_allowed": True,
        })
        subcatalogs.append(subcatalog)
        for dataset_key in member_keys:
            memberships[dataset_key].add(subcatalog["catalog_id"])
    return subcatalogs, memberships


def adapt(manifest: dict[str, Any]) -> dict[str, Any]:
    catalogs, source_pages = [], []
    records_by_id: dict[str, dict[str, Any]] = {}
    excluded_sources = [dict(row) for row in (manifest.get("restricted_source_exclusions") or []) if isinstance(row, dict)]
    excluded_source_records = sum(max(0, int(row.get("records_excluded") or 0)) for row in excluded_sources)
    excluded_source_catalogs = sum(max(0, int(row.get("catalogs_excluded") or 0)) for row in excluded_sources)
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
        source_terms_approved = (
            source_policy.get("discovery_allowed") is True
            and source_terms_status in ("reviewed", "reviewed_per_dataset")
            and bool(source_terms_reviewed_at)
        )
        if not source_terms_approved:
            excluded_sources.append({
                "catalog_id": source_id,
                "source_url": source.get("source_url") or base,
                "source_terms_status": source_terms_status,
                "records_excluded": len(datasets),
                "catalogs_excluded": 1,
                "reason": "source policy does not authorize discovery or source terms are not reviewed",
            })
            excluded_source_records += len(datasets)
            excluded_source_catalogs += 1
            continue
        catalog_policy_approved = source_terms_approved and catalog_type != "library_archive"
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
        dataset_rights = {
            id(dataset): _dataset_rights(dataset, catalog_policy_approved)
            for dataset in datasets
        }
        public_datasets = [
            dataset for dataset in datasets
            if dataset_rights[id(dataset)]["public_search_allowed"]
        ]
        subcatalogs, subcatalog_memberships = (
            _build_subcatalogs(
                source_id, catalog, public_datasets, source_policy, manifest.get("updated_at") or ""
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
            rights = dataset_rights[id(dataset)]
            license_info = rights["license"]
            public_search_allowed = rights["public_search_allowed"]
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
                "rights_status": rights["rights_status"],
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
                existing["source_provenance"] = sorted(
                    provenance_rows,
                    key=lambda row: (row.get("catalog_id", ""), row.get("source_url", "")),
                )
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
    metrics["restricted_source_catalogs_excluded"] = excluded_source_catalogs
    metrics["restricted_source_records_excluded"] = excluded_source_records
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
        "excluded_sources": excluded_sources,
        "source_pages": source_pages,
    }


def _canonical_json_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def build_public_catalog_indexes(result):
    """Build deterministic per-catalog index shards from rights-cleared records only."""
    catalogs = result.get("catalogs") or []
    records = result.get("records") or []
    catalog_by_id = {}
    for catalog in catalogs:
        if not isinstance(catalog, dict):
            raise ValueError("catalog index input contains a non-object catalog")
        catalog_id = catalog.get("catalog_id")
        if not isinstance(catalog_id, str) or not catalog_id:
            raise ValueError("catalog index input contains a catalog without catalog_id")
        if catalog_id in catalog_by_id:
            raise ValueError(f"duplicate catalog_id in registry: {catalog_id}")
        catalog_by_id[catalog_id] = catalog

    for catalog in catalog_by_id.values():
        if catalog.get("catalog_level") != "subcatalog" or catalog.get("public_search_allowed") is not True:
            continue
        parent = catalog_by_id.get(catalog.get("parent_catalog_id") or "")
        if not parent or parent.get("public_search_allowed") is not True:
            raise ValueError("public subcatalog must inherit an approved parent catalog")
        if catalog.get("metadata_only") is not False or catalog.get("record_level_clearance") != "eligible_per_record":
            raise ValueError("public subcatalog must have per-record eligibility clearance")

    staged_record_ids = set()
    record_by_id = {}
    eligible_record_ids = set()
    memberships_by_catalog = defaultdict(set)
    staged_membership_count = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("catalog index input contains a non-object record")
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("catalog index input contains a record without record_id")
        if record_id in staged_record_ids:
            raise ValueError(f"duplicate canonical record_id: {record_id}")
        staged_record_ids.add(record_id)
        record_by_id[record_id] = record

        raw_memberships = record.get("catalog_ids")
        if not isinstance(raw_memberships, list) or not raw_memberships:
            raw_memberships = [record.get("catalog_id")]
        catalog_ids = sorted({
            value for value in raw_memberships if isinstance(value, str) and value
        })
        if not catalog_ids:
            raise ValueError(f"record has no catalog membership: {record_id}")
        missing_catalogs = [value for value in catalog_ids if value not in catalog_by_id]
        if missing_catalogs:
            raise ValueError(
                f"record {record_id} references missing catalogs: {missing_catalogs}"
            )
        staged_membership_count += len(catalog_ids)

        attributes = record.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        if attributes.get("public_search_allowed") is not True:
            continue
        eligible_record_ids.add(record_id)
        for catalog_id in catalog_ids:
            if catalog_by_id[catalog_id].get("public_search_allowed") is True:
                memberships_by_catalog[catalog_id].add(record_id)

    # Build bounded, populated entry-owned catalogs from records that share
    # an approved catalog membership. Every candidate is rights-cleared above;
    # the owner itself is excluded and ordering is deterministic.
    MAX_ENTRY_CATALOG_RECORDS = 50
    related_record_ids_by_owner = {}
    for owner_id in sorted(eligible_record_ids):
        owner = record_by_id[owner_id]
        owner_memberships = sorted({
            value for value in (owner.get("catalog_ids") or [owner.get("catalog_id")])
            if isinstance(value, str) and value and value in memberships_by_catalog
        })
        candidates = set()
        for shared_catalog_id in owner_memberships:
            candidates.update(memberships_by_catalog[shared_catalog_id])
        candidates.discard(owner_id)
        related_record_ids_by_owner[owner_id] = sorted(candidates)[:MAX_ENTRY_CATALOG_RECORDS]

    shard_rows = defaultdict(list)
    catalog_index_manifest = []
    indexed_record_ids = set()
    root_record_search_document_ids = set()
    root_record_search_document_occurrences = 0
    public_memberships = 0
    catalogs_with_public_records = 0
    for catalog_id, catalog in sorted(catalog_by_id.items()):
        record_ids = sorted(memberships_by_catalog.get(catalog_id, set()))
        if record_ids:
            catalogs_with_public_records += 1
        indexed_record_ids.update(record_ids)
        public_memberships += len(record_ids)

        record_search_documents = []
        if catalog.get("catalog_level") == "root" and catalog.get("public_search_allowed") is True:
            for record_id in record_ids:
                record = record_by_id[record_id]
                attributes = record.get("attributes") or {}
                if attributes.get("public_search_allowed") is not True:
                    raise ValueError("root record search document is not rights-eligible")
                source_url = record.get("source_url") or ""
                if not isinstance(source_url, str) or not source_url.startswith("https://"):
                    raise ValueError("root record search document source URL must use HTTPS")
                source_urls = sorted(set(record.get("source_urls") or [source_url]))
                if source_url not in source_urls:
                    source_urls.append(source_url)
                    source_urls.sort()
                if not source_urls or any(not isinstance(value, str) or not value.startswith("https://") for value in source_urls):
                    raise ValueError("root record search document provenance must use HTTPS")
                raw_tags = attributes.get("tags") or []
                if isinstance(raw_tags, str):
                    raw_tags = [raw_tags]
                if not isinstance(raw_tags, list):
                    raw_tags = []
                tags = [str(value).strip()[:120] for value in raw_tags[:20] if str(value).strip()]
                title = str(record.get("title") or "").strip()[:500]
                record_type = str(record.get("record_type") or "dataset").strip()[:80]
                subject = str(record.get("subject") or "").strip()[:500]
                geography = str(record.get("geography") or attributes.get("geography") or "").strip()[:300]
                memberships = sorted({
                    value for value in (record.get("catalog_ids") or [record.get("catalog_id")])
                    if isinstance(value, str) and value
                })
                if not title or not record_type or catalog_id not in memberships:
                    raise ValueError("root record search document identity or catalog membership is invalid")
                search_parts = [
                    title,
                    record_type,
                    subject,
                    geography,
                    str(attributes.get("organization") or ""),
                    str(attributes.get("category") or ""),
                    *tags,
                ]
                search_text = " ".join(str(value).strip() for value in search_parts if str(value).strip())[:1200]
                record_catalog_id = "record-catalog:" + hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:24]
                record_catalog = {
                    "catalog_id": record_catalog_id,
                    "record_catalog_id": record_catalog_id,
                    "owner_record_id": record_id,
                    "catalog_kind": "record_related_items",
                    "membership_mode": "populated_shared_record_index",
                    "record_ids": related_record_ids_by_owner.get(record_id, []),
                    "record_count": len(related_record_ids_by_owner.get(record_id, [])),
                    "shared_catalog_ids": memberships,
                    "source_urls": source_urls[:5],
                    "rights_status": str(attributes.get("rights_status") or "review_required")[:100],
                    "source_terms_status": str(attributes.get("source_terms_status") or "not_reviewed")[:100],
                    "public_search_allowed": True,
                }
                record_search_documents.append({
                    "record_id": record_id,
                    "record_catalog_id": record_catalog_id,
                    "related_catalog": record_catalog,
                    "title": title,
                    "record_type": record_type,
                    "source_url": source_url,
                    "source_urls": source_urls[:5],
                    "subject": subject,
                    "geography": geography,
                    "search_text": search_text,
                    "catalog_ids": memberships,
                    "last_seen_at": str(record.get("last_seen_at") or "")[:64],
                    "public_search_allowed": True,
                    "rights_status": str(attributes.get("rights_status") or "review_required")[:100],
                    "source_terms_status": str(attributes.get("source_terms_status") or "not_reviewed")[:100],
                    "attribution_required": str(attributes.get("attribution_required") or "")[:100],
                })
                root_record_search_document_ids.add(record_id)
                root_record_search_document_occurrences += 1

        shard_id = hashlib.sha256(catalog_id.encode("utf-8")).hexdigest()[:2]
        index_file = f"shards/{shard_id}.json.gz"
        index_sha256 = hashlib.sha256(_canonical_json_bytes(record_ids)).hexdigest()
        staged_record_count = int(catalog.get("record_count") or 0)
        catalog["staged_record_count"] = staged_record_count
        catalog["public_record_count"] = len(record_ids)
        catalog["public_index_shard"] = index_file
        catalog["public_index_sha256"] = index_sha256
        catalog["public_index_schema_version"] = 1

        shard_rows[shard_id].append({
            "catalog_id": catalog_id,
            "catalog_level": catalog.get("catalog_level") or "root",
            "parent_catalog_id": catalog.get("parent_catalog_id") or "",
            "public_search_allowed": catalog.get("public_search_allowed") is True,
            "record_count": len(record_ids),
            "record_ids": record_ids,
            "record_search_document_count": len(record_search_documents),
            "record_search_documents": record_search_documents,
            "index_sha256": index_sha256,
        })
        catalog_index_manifest.append({
            "catalog_id": catalog_id,
            "catalog_level": catalog.get("catalog_level") or "root",
            "parent_catalog_id": catalog.get("parent_catalog_id") or "",
            "title": str(catalog.get("title") or catalog_id)[:500],
            "catalog_type": str(catalog.get("catalog_type") or "open_data")[:80],
            "subject": str(catalog.get("subject") or "")[:300],
            "publisher": str(catalog.get("publisher") or "")[:300],
            "geography": str(catalog.get("geography") or "")[:300],
            "source_url": str(catalog.get("source_url") or ""),
            "source_terms_url": str(catalog.get("source_terms_url") or ""),
            "source_terms_status": str(catalog.get("source_terms_status") or "not_reviewed"),
            "source_terms_reviewed_at": str(catalog.get("source_terms_reviewed_at") or ""),
            "attribution_required": str(catalog.get("attribution_required") or ""),
            "subcategory_kind": str(catalog.get("subcategory_kind") or ""),
            "subcategory_value": str(catalog.get("subcategory_value") or ""),
            "record_level_clearance": str(catalog.get("record_level_clearance") or (
                "eligible_per_record" if catalog.get("public_search_allowed") is True else "not_reviewed"
            )),
            "public_search_allowed": catalog.get("public_search_allowed") is True,
            "file": index_file,
            "record_count": len(record_ids),
            "staged_record_count": staged_record_count,
            "record_search_document_count": len(record_search_documents),
            "index_sha256": index_sha256,
        })

    eligible_without_index = eligible_record_ids - indexed_record_ids
    if eligible_without_index:
        raise ValueError(
            f"{len(eligible_without_index)} rights-cleared records have no public catalog membership"
        )
    if root_record_search_document_ids != eligible_record_ids:
        missing = len(eligible_record_ids - root_record_search_document_ids)
        extra = len(root_record_search_document_ids - eligible_record_ids)
        raise ValueError(
            f"root record search documents do not cover the rights-cleared record set: missing={missing}, extra={extra}"
        )

    shard_payloads = {}
    shard_manifest = []
    for shard_id, rows in sorted(shard_rows.items()):
        rows = sorted(rows, key=lambda row: row["catalog_id"])
        payload = {
            "schema_version": 1,
            "shard_id": shard_id,
            "catalog_indexes": rows,
        }
        compressed = gzip.compress(_canonical_json_bytes(payload), mtime=0)
        index_file = f"shards/{shard_id}.json.gz"
        shard_payloads[index_file] = compressed
        shard_manifest.append({
            "shard_id": shard_id,
            "file": index_file,
            "catalog_count": len(rows),
            "public_record_memberships": sum(row["record_count"] for row in rows),
            "record_search_document_occurrences": sum(row["record_search_document_count"] for row in rows),
            "bytes": len(compressed),
            "sha256": hashlib.sha256(compressed).hexdigest(),
        })

    metrics = result.setdefault("metrics", {})
    source_page_associations = int(metrics.get("source_page_associations") or 0)
    duplicate_record_occurrences = max(0, source_page_associations - len(staged_record_ids))
    metrics.update({
        "duplicate_record_occurrences": duplicate_record_occurrences,
        "catalog_indexes_built": len(catalog_by_id),
        "catalogs_with_public_records": catalogs_with_public_records,
        "catalogs_without_public_records": len(catalog_by_id) - catalogs_with_public_records,
        "unique_public_records_indexed": len(indexed_record_ids),
        "public_record_catalog_memberships": public_memberships,
        "restricted_records_excluded_from_public_indexes": len(staged_record_ids - eligible_record_ids),
        "eligible_records_without_public_index": len(eligible_without_index),
        "catalog_index_shards": len(shard_payloads),
        "record_owned_catalogs": len(root_record_search_document_ids),
        "record_owned_catalog_membership_mode": "populated_shared_record_index",
        "record_owned_catalog_related_record_memberships": sum(len(ids) for ids in related_record_ids_by_owner.values()),
    })

    manifest = {
        "schema_version": 1,
        "strategy": "rights-filtered-per-catalog-indexes",
        "generated_at": result.get("generated_at") or "",
        "production_indexed": False,
        "records_published": 0,
        "catalog_count": len(catalog_by_id),
        "catalogs_with_public_records": catalogs_with_public_records,
        "catalogs_without_public_records": len(catalog_by_id) - catalogs_with_public_records,
        "unique_public_records": len(indexed_record_ids),
        "public_record_memberships": public_memberships,
        "staged_unique_records": len(staged_record_ids),
        "staged_record_catalog_memberships": staged_membership_count,
        "restricted_records_not_indexed": len(staged_record_ids - eligible_record_ids),
        "eligible_records_without_public_index": len(eligible_without_index),
        "source_page_associations": source_page_associations,
        "index_shard_count": len(shard_payloads),
        "record_search_schema_version": 1,
        "root_record_search_document_occurrences": root_record_search_document_occurrences,
        "unique_root_record_search_documents": len(root_record_search_document_ids),
        "record_owned_catalog_count": len(root_record_search_document_ids),
        "record_owned_catalog_membership_mode": "populated_shared_record_index",
        "record_owned_catalog_related_record_memberships": sum(len(ids) for ids in related_record_ids_by_owner.values()),
        "catalog_indexes": catalog_index_manifest,
        "shards": shard_manifest,
    }
    return manifest, shard_payloads


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
        restricted_exclusions = list(manifest.get("restricted_source_exclusions") or [])
        if extra.get("source_policy_status") == "restricted":
            restricted_exclusions.append({
                "catalog_id": "openlibrary",
                "source_url": "https://openlibrary.org",
                "source_terms_status": extra.get("source_policy_status"),
                "records_excluded": max(0, int(extra.get("previous_snapshot_records_excluded") or 0)),
                "catalogs_excluded": max(0, int(extra.get("previous_snapshot_catalogs_excluded") or 0)),
                "reason": extra.get("reason") or "source policy restricts bulk discovery",
            })
        manifest = {
            **manifest,
            "catalogs": manifest.get("catalogs", []) + extra.get("catalogs", []),
            "datasets": manifest.get("datasets", []) + extra.get("datasets", []),
            "restricted_source_exclusions": restricted_exclusions,
        }
    result = adapt(manifest)
    index_manifest, index_shards = build_public_catalog_indexes(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if args.output.suffix == ".gz":
        temp.write_bytes(gzip.compress(payload, mtime=0))
    else:
        temp.write_bytes(payload)
    temp.replace(args.output)

    index_dir = args.output.parent / "universal-catalog-indexes"
    shard_dir = index_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    for stale_shard in shard_dir.glob("*.json.gz"):
        stale_shard.unlink()
    for relative_path, shard_bytes in sorted(index_shards.items()):
        shard_path = index_dir / relative_path
        shard_path.parent.mkdir(parents=True, exist_ok=True)
        shard_temp = shard_path.with_suffix(shard_path.suffix + ".tmp")
        shard_temp.write_bytes(shard_bytes)
        shard_temp.replace(shard_path)
    manifest_path = index_dir / "manifest.json"
    manifest_temp = manifest_path.with_suffix(".json.tmp")
    manifest_temp.write_bytes(_canonical_json_bytes(index_manifest))
    manifest_temp.replace(manifest_path)
    print(json.dumps(result["metrics"], indent=2))

if __name__ == "__main__":
    main()
