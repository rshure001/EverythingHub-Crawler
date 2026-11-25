"""Compact, provenance-preserving related-item catalogs for canonical records.

A record-owned catalog stores references to shared catalog indexes, not copied
record lists. Related items are resolved lazily and paginated at query time.
This module is deliberately side-effect free; callers decide when to publish.
"""
from __future__ import annotations

import hashlib
import heapq
from collections.abc import Iterator, Mapping
from typing import Any


def stable_record_catalog_id(record_id: str) -> str:
    """Return a deterministic ID for a canonical record."""
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("record_id must be a non-empty string")
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:24]
    return f"record-catalog:{digest}"


def build_related_catalog_definition(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact per-record catalog definition using shared indexes."""
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("record must contain a non-empty record_id")

    def strings(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return sorted({item for item in value if isinstance(item, str) and item.strip()})

    return {
        "catalog_id": stable_record_catalog_id(record_id),
        "record_catalog_id": stable_record_catalog_id(record_id),
        "owner_record_id": record_id,
        "catalog_kind": "record_related_items",
        "membership_mode": "virtual_shared_catalog_references",
        "shared_catalog_ids": strings(record.get("catalog_ids")),
        "source_page_ids": strings(record.get("source_page_ids")),
        "source_urls": strings(record.get("source_urls")),
        "last_seen_at": record.get("last_seen_at"),
        "source_terms_status": record.get("source_terms_status", "not_reviewed"),
        "rights_status": record.get(
            "rights_status", "publicly_accessible_not_cleared_for_redistribution"
        ),
        "record_level_clearance": record.get("record_level_clearance", "not_reviewed"),
        "commercial_eligible": record.get("commercial_eligible", False) is True,
        "public_search_allowed": record.get("public_search_allowed", False) is True,
    }


def _sorted_member_iter(values: Any) -> Iterator[str]:
    if not isinstance(values, (list, tuple)):
        return iter(())
    # Analyzer-produced lists take the zero-copy fast path.
    if all(isinstance(item, str) for item in values):
        if all(values[i] <= values[i + 1] for i in range(len(values) - 1)):
            return iter(values)
        return iter(sorted(set(values)))
    return iter(sorted({item for item in values if isinstance(item, str)}))


def iter_related_records(
    owner_record_id: str,
    related_catalog: Mapping[str, Any],
    catalogs_by_id: Mapping[str, Mapping[str, Any]],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> Iterator[Mapping[str, Any]]:
    """Stream a deduplicated union of shared catalog members, excluding owner."""
    catalog_ids = related_catalog.get("shared_catalog_ids", [])
    if not isinstance(catalog_ids, (list, tuple, set)):
        return
    iterators: list[Iterator[str]] = []
    for catalog_id in sorted({x for x in catalog_ids if isinstance(x, str)}):
        catalog = catalogs_by_id.get(catalog_id)
        if isinstance(catalog, Mapping):
            iterators.append(_sorted_member_iter(catalog.get("record_ids", [])))

    previous: str | None = None
    for related_id in heapq.merge(*iterators):
        if related_id == previous:
            continue
        previous = related_id
        if related_id == owner_record_id:
            continue
        record = records_by_id.get(related_id)
        if isinstance(record, Mapping):
            # Defer copying until the requested page is assembled.
            yield record


def resolve_related_records(
    owner_record_id: str,
    related_catalog: Mapping[str, Any],
    catalogs_by_id: Mapping[str, Mapping[str, Any]],
    records_by_id: Mapping[str, Mapping[str, Any]],
    *,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Resolve a stable, deduplicated page of related records."""
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    iterator = iter_related_records(owner_record_id, related_catalog, catalogs_by_id, records_by_id)
    page: list[dict[str, Any]] = []
    skipped = 0
    has_more = False
    for record in iterator:
        if skipped < offset:
            skipped += 1
            continue
        if len(page) == limit:
            has_more = True
            break
        page.append(dict(record))
    return {
        "owner_record_id": owner_record_id,
        "record_catalog_id": related_catalog.get("record_catalog_id"),
        "items": page,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "membership_mode": "virtual_shared_catalog_references",
    }
