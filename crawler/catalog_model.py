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

SCHEMA_VERSION = 1
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
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": stable_id("catalog", source_id),
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
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": stable_id(catalog_id, source_key),
        "catalog_id": catalog_id,
        "record_type": record_type,
        "title": title,
        "description": _text(raw.get("description"), 4000),
        "source_key": source_key,
        "source_url": source_url,
        "canonical_entity_key": _text(raw.get("canonical_entity_key"), 500),
        "subject": _text(raw.get("subject"), 200),
        "geography": _text(raw.get("geography"), 200),
        "attributes": attrs,
        "last_seen_at": _text(raw.get("last_seen_at"), 80),
    }


def catalog_metrics(catalogs: list[dict[str, Any]], records: list[dict[str, Any]],
                    source_pages: list[dict[str, Any]]) -> dict[str, int]:
    """Return distinct counters; never infer record totals from page totals."""
    return {
        "catalogs_discovered": len({c["catalog_id"] for c in catalogs}),
        "catalog_records": len({r["record_id"] for r in records}),
        "unique_entities": len({
            r.get("canonical_entity_key") or r["record_id"] for r in records
        }),
        "source_pages": len({
            _text(p.get("canonical_url") or p.get("url")) for p in source_pages
            if p.get("canonical_url") or p.get("url")
        }),
        "indexed_records": sum(1 for r in records if r.get("indexed") is True),
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
