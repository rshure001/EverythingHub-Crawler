#!/usr/bin/env python3
"""Bounded, metadata-only extraction from approved Frontiers catalog landing pages.

This fetches only robots-approved catalog listing pages, rechecks source terms
and robots at request time, extracts bibliographic metadata, and stores no
abstracts, full text, images, PDFs, or bulk assets. Output is candidate staging,
never production publication or rights clearance.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from catalog_host_probe import _landing_policy_fingerprint
from catalog_host_review_queue import load_verified_registry
from catalog_model import stable_id
from catalog_source_policy import SourcePolicyError, request_source_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROBOTS_LEDGER = ROOT / "data" / "catalog-landing-page-robots.json"
DEFAULT_CATALOG_MANIFEST = ROOT / "data" / "page-derived-catalogs" / "manifest.json"
DEFAULT_REGISTRY = ROOT / "data" / "page-derived-catalogs" / "registry.json.gz"
DEFAULT_OUTPUT = ROOT / "data" / "frontiers-metadata-candidates.json.gz"
MAX_PAGES_PER_RUN = 5
MAX_RESPONSE_BYTES = 1_500_000
ROBOTS_RECHECK_SECONDS = 86_400
FRONTIERS_HOSTS = {"frontiersin.org", "www.frontiersin.org"}
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _clean(value: object, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_doi(value: object) -> str:
    if isinstance(value, dict):
        prop = _clean(value.get("propertyID") or value.get("name"), 80).casefold()
        if "doi" in prop:
            value = value.get("value") or value.get("@id") or value.get("name")
        else:
            value = value.get("@id") or value.get("value") or value.get("url") or ""
    if isinstance(value, list):
        for item in value:
            normalized = normalize_doi(item)
            if normalized:
                return normalized
        return ""
    raw = unquote(_clean(value, 1000))
    raw = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", raw, flags=re.IGNORECASE)
    match = DOI_RE.search(raw)
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)]}").casefold()


def _as_text(value: object, key: str = "name") -> str:
    if isinstance(value, dict):
        return _clean(value.get(key) or value.get("@id") or value.get("url"), 500)
    if isinstance(value, list):
        return _as_text(value[0], key) if value else ""
    return _clean(value, 500)


def _names(value: object) -> list[str]:
    rows = value if isinstance(value, list) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = _as_text(row)
        folded = name.casefold()
        if name and folded not in seen:
            seen.add(folded)
            result.append(name)
        if len(result) >= 50:
            break
    return result


def _license_values(value: object) -> list[str]:
    rows = value if isinstance(value, list) else [value]
    result: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            candidate = row.get("url") or row.get("@id") or row.get("name")
        else:
            candidate = row
        text = _clean(candidate, 1000)
        if text and text not in result:
            result.append(text)
    return result[:10]


def _license_classification(values: list[str]) -> str:
    joined = " ".join(values).casefold()
    if "creativecommons.org/publicdomain/zero" in joined or "cc0" in joined:
        return "cc0_evidence"
    if "creativecommons.org/licenses/by/" in joined or "cc-by" in joined:
        return "cc_by_evidence"
    if values:
        return "other_or_unverified_license"
    return "license_missing"


class _ListingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self._inside_jsonld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script" and "ld+json" in values.get("type", "").casefold():
            self._inside_jsonld = True
            self._script_parts = []
        if tag.casefold() == "meta":
            key = (values.get("name") or values.get("property") or values.get("itemprop") or "").casefold()
            content = values.get("content", "")
            if key and content:
                self.meta.setdefault(key, []).append(content)

    def handle_data(self, data: str) -> None:
        if self._inside_jsonld:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._inside_jsonld:
            joined = "".join(self._script_parts).strip()
            if joined:
                self.jsonld.append(joined)
            self._inside_jsonld = False
            self._script_parts = []


def _walk_jsonld(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)
        return
    if not isinstance(value, dict):
        return
    kind = value.get("@type")
    kinds = {str(x).casefold() for x in (kind if isinstance(kind, list) else [kind]) if x}
    if kinds.intersection({"scholarlyarticle", "article"}):
        yield value
    for child in value.values():
        if isinstance(child, (dict, list)):
            yield from _walk_jsonld(child)


def _article_url(node: dict[str, Any], doi: str) -> str:
    candidates = [node.get("url"), node.get("mainEntityOfPage"), node.get("@id"), node.get("sameAs")]
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else [candidate]
        for item in values:
            url = _as_text(item, "url") if isinstance(item, dict) else _clean(item, 2000)
            if not url.startswith("https://"):
                continue
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if parsed.hostname and parsed.scheme == "https" and parsed.username is None and parsed.password is None:
                if parsed.hostname.casefold() in FRONTIERS_HOSTS and "/articles/" in parsed.path:
                    return url.split("#", 1)[0]
    return "https://doi.org/" + doi


def _journal_name(node: dict[str, Any]) -> str:
    return (
        _as_text(node.get("isPartOf"))
        or _as_text(node.get("periodical"))
        or _as_text(node.get("journal"))
    )


def _publisher_name(node: dict[str, Any]) -> str:
    return _as_text(node.get("publisher"))


def _record_from_node(
    node: dict[str, Any],
    *,
    source_url: str,
    catalog_ids: list[str],
    policy: dict[str, Any],
    fetched_at: str,
) -> dict[str, Any] | None:
    doi = (
        normalize_doi(node.get("doi"))
        or normalize_doi(node.get("identifier"))
        or normalize_doi(node.get("sameAs"))
        or normalize_doi(node.get("@id"))
        or normalize_doi(node.get("url"))
    )
    title = _clean(node.get("headline") or node.get("name") or node.get("title"), 500)
    if not doi or not title:
        return None
    licenses = _license_values(node.get("license"))
    catalog_ids = sorted({x for x in catalog_ids if isinstance(x, str) and x})
    if not catalog_ids:
        return None
    record_id = stable_id("frontiers-doi", doi)
    article_url = _article_url(node, doi)
    license_classification = _license_classification(licenses)
    attributes = {
        "doi": doi,
        "authors": _names(node.get("author")),
        "journal": _journal_name(node),
        "publication_date": _clean(node.get("datePublished") or node.get("dateCreated"), 80),
        "license": licenses,
        "license_classification": license_classification,
        "publisher": _publisher_name(node),
        "source_listing_urls": [source_url],
        "source_terms_url": _clean(policy.get("terms_url"), 1000),
        "source_terms_status": _clean(policy.get("terms_status"), 80),
        "source_terms_reviewed_at": _clean(policy.get("reviewed_at"), 80),
        "attribution_required": "per_article",
        "metadata_only": True,
        "allowed_metadata_fields": [
            "title", "authors", "doi", "journal", "publication_date",
            "article_url", "license", "publisher",
        ],
        "abstract_stored": False,
        "full_text_stored": False,
        "images_stored": False,
        "bulk_assets_downloaded": False,
        "record_level_rights_status": "review_required",
        "public_search_allowed": False,
        "commercial_eligible": False,
    }
    return {
        "record_id": record_id,
        "record_type": "article",
        "title": title,
        "description": "",
        "source_key": "doi:" + doi,
        "canonical_entity_key": "doi:" + doi,
        "source_url": article_url,
        "catalog_id": catalog_ids[0],
        "catalog_ids": catalog_ids,
        "subject": _journal_name(node),
        "attributes": attributes,
        "first_seen_at": fetched_at,
        "last_seen_at": fetched_at,
        "source_listing_urls": [source_url],
        "status": "candidate_metadata_only_not_production",
    }


def extract_articles(
    html: str,
    *,
    source_url: str,
    catalog_ids: list[str],
    policy: dict[str, Any],
    fetched_at: str | None = None,
) -> list[dict[str, Any]]:
    """Extract only approved bibliographic fields; never retain abstract/body/media."""
    parser = _ListingHTMLParser()
    parser.feed(html)
    when = fetched_at or _utc_now()
    nodes: list[dict[str, Any]] = []
    for script in parser.jsonld:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        nodes.extend(node for node in _walk_jsonld(payload) if isinstance(node, dict))

    if not nodes:
        meta = parser.meta
        doi = normalize_doi(
            (meta.get("citation_doi") or meta.get("dc.identifier") or [""])[0]
        )
        title = _clean(
            (meta.get("citation_title") or meta.get("og:title") or meta.get("dc.title") or [""])[0],
            500,
        )
        if doi and title:
            authors = meta.get("citation_author") or meta.get("dc.creator") or []
            node = {
                "@type": "ScholarlyArticle",
                "doi": doi,
                "headline": title,
                "author": authors,
                "isPartOf": (meta.get("citation_journal_title") or [""])[0],
                "datePublished": (meta.get("citation_publication_date") or [""])[0],
                "license": (meta.get("citation_license") or []),
                "url": (meta.get("citation_public_url") or [""])[0],
            }
            nodes.append(node)

    records: dict[str, dict[str, Any]] = {}
    for node in nodes:
        record = _record_from_node(
            node,
            source_url=source_url,
            catalog_ids=catalog_ids,
            policy=policy,
            fetched_at=when,
        )
        if not record:
            continue
        key = record["canonical_entity_key"]
        existing = records.get(key)
        if existing:
            merge_article_record(existing, record)
        else:
            records[key] = record
    return [records[key] for key in sorted(records)]


def merge_article_record(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Merge duplicate DOI evidence across listing pages and catalog memberships."""
    for key in ("catalog_ids", "source_listing_urls"):
        merged = sorted(set(existing.get(key, [])) | set(incoming.get(key, [])))
        existing[key] = merged
    existing["catalog_id"] = existing["catalog_ids"][0] if existing["catalog_ids"] else existing.get("catalog_id")
    attrs = existing.setdefault("attributes", {})
    incoming_attrs = incoming.get("attributes") or {}
    for key in ("authors", "journal", "publication_date", "license", "publisher"):
        current = attrs.get(key)
        candidate = incoming_attrs.get(key)
        if (not current or current == [] or current == "") and candidate:
            attrs[key] = candidate
    attrs["source_listing_urls"] = existing["source_listing_urls"]
    existing["last_seen_at"] = max(existing.get("last_seen_at", ""), incoming.get("last_seen_at", ""))


def _load_output(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 1,
            "status": "candidate_metadata_staging_not_production",
            "analysis_version": None,
            "source_index_pages": None,
            "source_index_sha256": None,
            "updated_at": None,
            "catalogs": [],
            "records": [],
            "source_pages": [],
            "metrics": {},
        }
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "candidate_metadata_staging_not_production":
        raise ValueError("existing Frontiers metadata artifact has an unsupported status")
    return payload


def _save_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == ".gz" else raw)
    temp.replace(path)


def _eligible_rows(ledger: dict[str, Any], manifest: dict[str, Any], registry: dict[str, Any], existing: dict[str, Any]) -> list[dict[str, Any]]:
    if ledger.get("status") != "robots_preflight_only_not_fetch_authorization":
        raise ValueError("robots ledger is not a robots-only preflight artifact")
    for key in ("analysis_version", "source_index_pages", "source_index_sha256"):
        if ledger.get(key) != manifest.get(key if key != "analysis_version" else "analysis_version"):
            raise ValueError("robots ledger is stale or does not match the catalog manifest")
    catalog_map = {
        row.get("catalog_id"): row
        for row in registry.get("catalogs", [])
        if isinstance(row, dict) and isinstance(row.get("catalog_id"), str)
    }
    prior = {
        row.get("source_url"): row
        for row in existing.get("source_pages", [])
        if isinstance(row, dict) and isinstance(row.get("source_url"), str)
    }
    now = datetime.now(timezone.utc)
    rows = []
    for row in ledger.get("results", []):
        if not isinstance(row, dict):
            continue
        if row.get("status") != "robots_allowed" or row.get("robots_allowed") is not True:
            continue
        if row.get("robots_check_performed") is not True or row.get("source_access_mode") != "catalog_landing_page":
            continue
        if row.get("review_status") != "policy_preapproved_robots_recheck_required":
            continue
        if row.get("metadata_only") is not True or row.get("fetch_authorized") is not False:
            continue
        if row.get("page_content_fetched") is not False or row.get("page_requests") != 0:
            continue
        if not isinstance(row.get("policy_fingerprint"), str) or row.get("policy_fingerprint") != _landing_policy_fingerprint(row):
            continue
        checked = _parse_time(row.get("checked_at"))
        if not checked or (now - checked).total_seconds() >= ROBOTS_RECHECK_SECONDS:
            continue
        url = row.get("source_url")
        try:
            parsed = urlsplit(str(url or ""))
            _ = parsed.port
        except ValueError:
            continue
        if parsed.scheme != "https" or parsed.hostname not in FRONTIERS_HOSTS:
            continue
        if not isinstance(row.get("catalog_ids"), list):
            continue
        catalog_ids = sorted({x for x in row["catalog_ids"] if isinstance(x, str) and x in catalog_map})
        if not catalog_ids:
            continue
        previous = prior.get(url)
        previous_attempt = _parse_time(previous.get("last_attempt_at") or previous.get("fetched_at")) if previous else None
        if previous_attempt and (now - previous_attempt).total_seconds() < ROBOTS_RECHECK_SECONDS:
            continue
        item = dict(row)
        item["catalog_ids"] = catalog_ids
        rows.append(item)
    rows.sort(key=lambda item: (item.get("checked_at", ""), item.get("source_url", "")))
    return rows


def run(
    *,
    limit: int = MAX_PAGES_PER_RUN,
    robots_ledger_path: Path = DEFAULT_ROBOTS_LEDGER,
    catalog_manifest_path: Path = DEFAULT_CATALOG_MANIFEST,
    registry_path: Path = DEFAULT_REGISTRY,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if not 1 <= limit <= 10:
        raise ValueError("metadata landing-page limit must be between 1 and 10")
    ledger = json.loads(robots_ledger_path.read_text(encoding="utf-8"))
    catalog_manifest, registry = load_verified_registry(catalog_manifest_path, registry_path)
    existing = _load_output(output_path)
    rows = _eligible_rows(ledger, catalog_manifest, registry, existing)
    records = {
        row.get("canonical_entity_key"): row
        for row in existing.get("records", [])
        if isinstance(row, dict) and isinstance(row.get("canonical_entity_key"), str)
    }
    source_pages = {
        row.get("source_url"): row
        for row in existing.get("source_pages", [])
        if isinstance(row, dict) and isinstance(row.get("source_url"), str)
    }
    catalog_map = {
        row.get("catalog_id"): row
        for row in registry.get("catalogs", [])
        if isinstance(row, dict) and isinstance(row.get("catalog_id"), str)
    }
    errors: list[dict[str, str]] = []
    pages_attempted = 0
    pages_fetched = 0
    duplicate_dois = 0
    extracted_observations = 0

    for item in rows[:limit]:
        url = item["source_url"]
        pages_attempted += 1
        attempted_at = _utc_now()
        try:
            raw, status, headers, policy = request_source_bytes(
                url,
                purpose="landing_page",
                max_bytes=MAX_RESPONSE_BYTES,
                timeout=8,
                accept="text/html",
            )
            if status != 200:
                raise ValueError("catalog landing page returned HTTP " + str(status))
            if policy.get("metadata_only") is not True or not policy.get("reviewed_at"):
                raise SourcePolicyError("runtime source policy is not a dated metadata-only review")
            if policy.get("terms_status") not in {"reviewed", "reviewed_per_dataset"}:
                raise SourcePolicyError("runtime source terms are not reviewed")
            allowed_fields = set(policy.get("allowed_metadata_fields") or [])
            required_fields = {"title", "authors", "doi", "journal", "publication_date", "article_url", "license", "publisher"}
            if not required_fields.issubset(allowed_fields):
                raise SourcePolicyError("runtime source policy does not allow the required metadata fields")
            prohibited = set(policy.get("prohibited_content") or [])
            if not {"full_text", "images", "supplementary_files", "bulk_assets"}.issubset(prohibited):
                raise SourcePolicyError("runtime source policy does not prohibit full-text and bulk assets")
            fetched_at = _utc_now()
            html = raw.decode("utf-8", errors="replace")
            extracted = extract_articles(
                html,
                source_url=url,
                catalog_ids=item["catalog_ids"],
                policy=policy,
                fetched_at=fetched_at,
            )
            pages_fetched += 1
            extracted_observations += len(extracted)
            page_record_ids = []
            for record in extracted:
                key = record["canonical_entity_key"]
                if key in records:
                    merge_article_record(records[key], record)
                    duplicate_dois += 1
                else:
                    records[key] = record
                page_record_ids.append(records[key]["record_id"])
            source_pages[url] = {
                "source_url": url,
                "canonical_url": url,
                "source_host": item.get("source_host"),
                "catalog_ids": item["catalog_ids"],
                "fetched": True,
                "fetch_authorized": True,
                "robots_rechecked": True,
                "source_terms_url": policy.get("terms_url"),
                "source_terms_status": policy.get("terms_status"),
                "source_terms_reviewed_at": policy.get("reviewed_at"),
                "fetched_at": fetched_at,
                "last_attempt_at": attempted_at,
                "http_status": status,
                "content_type": headers.get("Content-Type") or headers.get("content-type", ""),
                "response_bytes": len(raw),
                "metadata_only": True,
                "content_stored": False,
                "abstracts_stored": 0,
                "full_text_stored": False,
                "images_stored": 0,
                "bulk_assets_downloaded": 0,
                "articles_extracted": len(extracted),
                "record_ids": sorted(set(page_record_ids)),
            }
        except (SourcePolicyError, OSError, ValueError, UnicodeError) as exc:
            message = str(exc)[:240]
            status_value = "source_policy_blocked" if isinstance(exc, SourcePolicyError) else "fetch_or_parse_error"
            source_pages[url] = {
                "source_url": url,
                "canonical_url": url,
                "source_host": item.get("source_host"),
                "catalog_ids": item["catalog_ids"],
                "fetched": False,
                "fetch_authorized": False,
                "robots_rechecked": isinstance(exc, SourcePolicyError) and "robots.txt" in message,
                "last_attempt_at": attempted_at,
                "status": status_value,
                "error": message,
                "content_stored": False,
                "record_ids": [],
            }
            errors.append({"source_url": url, "status": status_value, "error": message})

    catalog_record_ids: dict[str, set[str]] = {}
    for record in records.values():
        for catalog_id in record.get("catalog_ids", []):
            catalog_record_ids.setdefault(catalog_id, set()).add(record["record_id"])
    catalogs = []
    for catalog_id in sorted(catalog_record_ids):
        source = catalog_map.get(catalog_id)
        if not source:
            continue
        record_ids = sorted(catalog_record_ids[catalog_id])
        catalogs.append({
            "catalog_id": catalog_id,
            "title": source.get("title"),
            "catalog_type": source.get("catalog_type"),
            "source_url": source.get("source_url"),
            "record_count": len(record_ids),
            "record_ids": record_ids,
            "status": "candidate_metadata_only_not_production",
        })

    ordered_records = sorted(records.values(), key=lambda row: row["canonical_entity_key"])
    ordered_pages = sorted(source_pages.values(), key=lambda row: row["source_url"])
    metrics = {
        "robots_approved_candidates": sum(1 for row in ledger.get("results", []) if row.get("status") == "robots_allowed"),
        "pages_attempted_this_run": pages_attempted,
        "pages_fetched_this_run": pages_fetched,
        "article_observations_this_run": extracted_observations,
        "duplicate_doi_observations_this_run": duplicate_dois,
        "pages_fetched_total": sum(1 for row in ordered_pages if row.get("fetched") is True),
        "unique_records": len(ordered_records),
        "unique_catalogs_with_records": len(catalogs),
        "records_staged": len(ordered_records),
        "records_published": 0,
        "public_search_allowed_records": 0,
        "record_level_rights_reviewed": 0,
        "commercially_eligible_records": 0,
        "abstracts_stored": 0,
        "full_text_stored": 0,
        "images_stored": 0,
        "bulk_assets_downloaded": 0,
        "errors_total": len(errors),
    }
    output = {
        "schema_version": 1,
        "status": "candidate_metadata_staging_not_production",
        "analysis_version": catalog_manifest.get("analysis_version"),
        "source_index_pages": catalog_manifest.get("source_index_pages"),
        "source_index_sha256": catalog_manifest.get("source_index_sha256"),
        "source_policy_scope": "Frontiers public scientific-literature landing-page metadata only",
        "updated_at": _utc_now(),
        "metrics": metrics,
        "catalogs": catalogs,
        "records": ordered_records,
        "source_pages": ordered_pages,
        "last_run_errors": errors,
    }
    _save_output(output_path, output)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=MAX_PAGES_PER_RUN)
    parser.add_argument("--robots-ledger", type=Path, default=DEFAULT_ROBOTS_LEDGER)
    parser.add_argument("--catalog-manifest", type=Path, default=DEFAULT_CATALOG_MANIFEST)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(
        limit=args.limit,
        robots_ledger_path=args.robots_ledger,
        catalog_manifest_path=args.catalog_manifest,
        registry_path=args.registry,
        output_path=args.output,
    ), indent=2))


if __name__ == "__main__":
    main()
