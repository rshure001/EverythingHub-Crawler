"""Universal catalog/record contract shared by subject-specific adapters.

Catalogs are collections; records are the entities inside them; source pages
are evidence and click-through locations. These counters intentionally never
collapse into one number.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 2
CATALOG_TYPES = {
    "commerce", "brand", "business_directory", "geographic", "government_registry",
    "open_data", "library_archive", "scholarly", "events", "media", "software",
    "knowledge_base", "general",
}
RECORD_TYPES = {
    "product", "brand", "business", "place", "event", "dataset", "document",
    "service", "model", "article", "listing", "person", "organization",
    "software", "media", "other",
}


def _text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _https_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("source URLs must be absolute HTTPS URLs")
    return raw


def stable_id(namespace: str, source_key: str) -> str:
    namespace = _text(namespace, 100).casefold()
    source_key = _text(source_key, 1000)
    if not namespace or not source_key:
        raise ValueError("namespace and source_key are required")
    digest = hashlib.sha256((namespace + "\\0" + source_key).encode("utf-8")).hexdigest()[:24]
    return namespace + ":" + digest


def normalize_catalog(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("catalog must be an object")
    catalog_type = _text(raw.get("catalog_type"), 60).casefold()
    if catalog_type not in CATALOG_TYPES:
        raise ValueError("unsupported catalog_type")
    source_url = _https_url(raw.get("source_url"))
    title = _text(raw.get("title"), 300)
    source_id = _text(raw.get("source_id"), 200)
    if not title or not source_id:
        raise ValueError("catalog title and source_id are required")
    catalog_id = stable_id("catalog", source_id)
    parent_catalog_id = _text(raw.get("parent_catalog_id"), 200)
    catalog_level = _text(
        raw.get("catalog_level") or ("subcatalog" if parent_catalog_id else "root"),
        20,
    ).casefold()
    if catalog_level not in {"root", "subcatalog"}:
        raise ValueError("catalog_level must be root or subcatalog")
    if catalog_level == "subcatalog" and not parent_catalog_id:
        raise ValueError("subcatalogs require parent_catalog_id")
    if catalog_level == "root" and parent_catalog_id:
        raise ValueError("root catalogs cannot have parent_catalog_id")
    if parent_catalog_id and parent_catalog_id == catalog_id:
        raise ValueError("catalog cannot be its own parent")
    subcategory_kind = _text(raw.get("subcategory_kind"), 40).casefold()
    subcategory_value = _text(raw.get("subcategory_value"), 200)
    if catalog_level == "subcatalog" and (not subcategory_kind or not subcategory_value):
        raise ValueError("subcatalogs require subcategory_kind and subcategory_value")
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": catalog_id,
        "catalog_level": catalog_level,
        "parent_catalog_id": parent_catalog_id,
        "subcategory_kind": subcategory_kind,
        "subcategory_value": subcategory_value,
        "source_id": source_id,
        "title": title,
        "description": _text(raw.get("description"), 4000),
        "catalog_type": catalog_type,
        "subject": _text(raw.get("subject"), 200),
        "publisher": _text(raw.get("publisher"), 300),
        "geography": _text(raw.get("geography"), 200),
        "source_url": source_url,
        "access_method": _text(raw.get("access_method") or "web", 40).casefold(),
        "coverage_status": _text(raw.get("coverage_status") or "unknown", 40).casefold(),
        "record_count": max(0, int(raw.get("record_count") or 0)),
        "last_discovered_at": _text(raw.get("last_discovered_at"), 80),
        "last_synced_at": _text(raw.get("last_synced_at"), 80),
    }


def normalize_record(raw: dict[str, Any], catalog_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("record must be an object")
    record_type = _text(raw.get("record_type"), 40).casefold()
    if record_type not in RECORD_TYPES:
        raise ValueError("unsupported record_type")
    source_key = _text(raw.get("source_key"), 1000)
    title = _text(raw.get("title"), 500)
    source_url = _https_url(raw.get("source_url"))
    if not source_key or not title or not catalog_id:
        raise ValueError("source_key, title and catalog_id are required")
    attrs = raw.get("attributes") or {}
    if not isinstance(attrs, dict):
        raise ValueError("attributes must be an object")
    # Keep attributes structured, bounded, and JSON-like. Source adapters own
    # field semantics; common search fields remain explicit.
    if len(attrs) > 100:
        raise ValueError("too many record attributes")
    canonical_entity_key = _text(raw.get("canonical_entity_key"), 500)
    record_namespace = "record:" + record_type if canonical_entity_key else catalog_id
    record_identity = canonical_entity_key or source_key
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": stable_id(record_namespace, record_identity),
        "catalog_id": catalog_id,
        "catalog_ids": [catalog_id],
        "record_type": record_type,
        "title": title,
        "description": _text(raw.get("description"), 4000),
        "source_key": source_key,
        "source_url": source_url,
        "source_urls": [source_url],
        "canonical_entity_key": canonical_entity_key,
        "subject": _text(raw.get("subject"), 200),
        "geography": _text(raw.get("geography"), 200),
        "attributes": attrs,
        "last_seen_at": _text(raw.get("last_seen_at"), 80),
    }


def catalog_metrics(catalogs: list[dict[str, Any]], records: list[dict[str, Any]],
                    source_pages: list[dict[str, Any]]) -> dict[str, int]:
    """Return distinct counters; memberships and source-page associations stay separate."""
    catalog_ids = {
        catalog["catalog_id"] for catalog in catalogs
        if isinstance(catalog, dict) and catalog.get("catalog_id")
    }
    subcatalog_ids = {
        catalog["catalog_id"] for catalog in catalogs
        if isinstance(catalog, dict)
        and catalog.get("catalog_id")
        and (catalog.get("catalog_level") == "subcatalog" or catalog.get("parent_catalog_id"))
    }
    record_ids = {
        record["record_id"] for record in records
        if isinstance(record, dict) and record.get("record_id")
    }
    memberships = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("record_id"):
            continue
        catalog_memberships = record.get("catalog_ids")
        if not isinstance(catalog_memberships, list) or not catalog_memberships:
            catalog_memberships = [record.get("catalog_id")]
        for catalog_id in catalog_memberships:
            if isinstance(catalog_id, str) and catalog_id:
                memberships.add((record["record_id"], catalog_id))
    catalogs_with_records = {catalog_id for _, catalog_id in memberships}
    referenced_urls = {
        _text(page.get("canonical_url") or page.get("url"))
        for page in source_pages
        if isinstance(page, dict) and (page.get("canonical_url") or page.get("url"))
    }
    fetched_urls = {
        _text(page.get("canonical_url") or page.get("url"))
        for page in source_pages
        if isinstance(page, dict)
        and (page.get("canonical_url") or page.get("url"))
        and page.get("fetched") is True
    }
    indexed_record_ids = {
        record["record_id"] for record in records
        if isinstance(record, dict) and record.get("record_id") and record.get("indexed") is True
    }
    published_record_ids = {
        record["record_id"] for record in records
        if isinstance(record, dict) and record.get("record_id") and record.get("published") is True
    }
    return {
        "catalogs_discovered": len(catalog_ids),
        "root_catalogs_discovered": len(catalog_ids - subcatalog_ids),
        "subcatalogs_discovered": len(subcatalog_ids),
        "catalogs_with_records": len(catalogs_with_records),
        "catalogs_without_records": len(catalog_ids - catalogs_with_records),
        "catalog_records": len(record_ids),
        "record_catalog_memberships": len(memberships),
        "unique_entities": len({
            (record.get("record_type", ""), record.get("canonical_entity_key") or record.get("record_id"))
            for record in records
            if isinstance(record, dict) and record.get("record_id")
        }),
        "source_page_references": len(referenced_urls),
        "source_page_associations": len(source_pages),
        "source_pages_fetched": len(fetched_urls),
        "indexed_records": len(indexed_record_ids),
        "records_published": len(published_record_ids),
    }


def query_catalogs(query: str, catalogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Simple deterministic baseline resolver; production ranking can replace it."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", query.casefold()) if len(t) > 1]
    if not tokens:
        return []
    ranked = []
    for catalog in catalogs:
        haystack = " ".join(str(catalog.get(k, "")) for k in (
            "title", "description", "subject", "publisher", "geography", "catalog_type"
        )).casefold()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            ranked.append((score, catalog))
    ranked.sort(key=lambda item: (-item[0], item[1].get("title", "").casefold()))
    return [item[1] for item in ranked]
