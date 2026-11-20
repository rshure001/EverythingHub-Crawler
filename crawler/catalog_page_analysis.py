#!/usr/bin/env python3
"""Build a provenance-preserving, deduplicated page-derived catalog candidate network.

This pass analyzes the existing canonical index only. It does not refetch source
pages, bypass robots.txt, import to PostgreSQL, or claim candidates are verified.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data" / "index.json"
DEFAULT_OUTPUT = ROOT / "data" / "page-derived-catalogs"
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "ref", "source"}
STOP_TOKENS = {"the", "and", "for", "with", "from", "our", "your", "all", "new", "home", "page", "official", "welcome"}
GENERIC_TITLES = {"home", "homepage", "welcome", "404", "not found", "access denied", "page not found"}
MEDIA_URL_RE = re.compile(r"https?://[^\s<>\"']+\.(?:jpg|jpeg|png|webp|gif|svg|mp4|webm|mov|mp3|wav|m4a)(?:\?[^\s<>\"']*)?", re.I)
LISTING_RE = re.compile(r"\b(?:browse|view all|all products|all models|filter by|sort by|search results|showing \d+|catalog(?:ue)?|directory|store locator|find a location)\b", re.I)
CATALOG_TITLE_RE = re.compile(r"\b(?:catalog(?:ue)?|collection|directory|all (?:products|models|services|locations|places|events|datasets|articles|brands)|browse all|shop all|store locator)\b", re.I)

SEGMENT_TYPES = {
    "products": "product", "product": "product", "items": "product", "item": "product",
    "shop": "product", "collections": "product", "collection": "product",
    "watches": "model", "models": "model", "model": "model",
    "brands": "brand", "brand": "brand",
    "services": "service", "service": "service", "support": "service", "repairs": "service",
    "businesses": "business", "business": "business", "stores": "business", "dealers": "business",
    "restaurants": "business", "hotels": "business", "directory": "business",
    "places": "place", "locations": "place", "location": "place", "parks": "place",
    "museums": "place", "attractions": "place", "destinations": "place",
    "people": "person", "person": "person", "authors": "person", "author": "person",
    "profiles": "person", "profile": "person", "staff": "person", "team": "person",
    "organizations": "organization", "organization": "organization", "agencies": "organization",
    "agency": "organization", "institutions": "organization", "foundation": "organization",
    "events": "event", "event": "event", "calendar": "event", "conferences": "event",
    "datasets": "dataset", "dataset": "dataset", "data": "dataset", "api": "dataset",
    "registries": "dataset", "registry": "dataset",
    "news": "article", "articles": "article", "article": "article", "blog": "article",
    "posts": "article", "post": "article", "research": "article", "publications": "article",
    "publication": "article", "papers": "article", "reports": "article", "archive": "article",
    "archives": "article", "library": "article",
    "media": "media", "images": "media", "videos": "media", "audio": "media", "gallery": "media",
    "software": "software", "packages": "software", "package": "software", "tools": "software",
    "libraries": "software", "projects": "software",
    "resources": "resource", "resource": "resource",
}
CATALOG_SEGMENTS = set(SEGMENT_TYPES)
ROOTS_BY_TYPE = {
    "product": {"products", "product", "items", "item", "shop", "collections", "collection"},
    "model": {"watches", "models", "model"},
    "brand": {"brands", "brand"},
    "service": {"services", "service", "support", "repairs"},
    "business": {"businesses", "business", "stores", "dealers", "restaurants", "hotels", "directory"},
    "place": {"places", "locations", "location", "parks", "museums", "attractions", "destinations"},
    "person": {"people", "person", "authors", "author", "profiles", "profile", "staff", "team"},
    "organization": {"organizations", "organization", "agencies", "agency", "institutions", "foundation"},
    "event": {"events", "event", "calendar", "conferences"},
    "dataset": {"datasets", "dataset", "data", "api", "registries", "registry"},
    "article": {"news", "articles", "article", "blog", "posts", "post", "research", "publications", "publication", "papers", "reports", "archive", "archives", "library"},
    "media": {"media", "images", "videos", "audio", "gallery"},
    "software": {"software", "packages", "package", "tools", "libraries", "projects"},
    "resource": {"resources", "resource"},
}
CATEGORY_SEGMENTS = {"watches", "phones", "smartphones", "laptops", "computers", "shoes", "cars", "books", "courses", "hotels", "restaurants", "parks", "museums", "attractions", "events", "models", "brands", "software", "datasets"}
CATALOG_TYPE = {
    "product": "commerce", "model": "commerce", "brand": "commerce",
    "service": "service_directory", "business": "business_directory",
    "place": "geographic", "person": "people_directory", "organization": "organization_directory",
    "event": "events", "dataset": "open_data", "article": "knowledge_base",
    "media": "media", "software": "software", "resource": "resource_library",
}
TYPE_LABEL = {
    "product": "Products", "model": "Models", "brand": "Brands", "service": "Services",
    "business": "Businesses", "place": "Places", "person": "People",
    "organization": "Organizations", "event": "Events", "dataset": "Datasets",
    "article": "Articles", "media": "Media", "software": "Software", "resource": "Resources",
}

def stable_id(prefix: str, key: str) -> str:
    return prefix + ":" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def canonical_url(value: str) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if port and not ((parsed.scheme.lower() == "https" and port == 443) or (parsed.scheme.lower() == "http" and port == 80)):
        host += ":" + str(port)
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
    query.sort()
    return urlunsplit((parsed.scheme.lower(), host, path, urlencode(query, doseq=True), ""))


def _title(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:240]


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _host_label(host: str) -> str:
    first = host.split(".", 1)[0].replace("-", " ").replace("_", " ")
    return " ".join(x.capitalize() for x in first.split())


def _path_segments(url: str) -> list[str]:
    return [x.casefold() for x in urlsplit(url).path.split("/") if x]


def _valid_title(value: str) -> bool:
    return len(value) >= 3 and _norm(value) not in GENERIC_TITLES and not value.casefold().startswith(("error ", "403 ", "404 "))


def _record_type(page: dict, segments: list[str]) -> str | None:
    lane = str(page.get("lane") or "").casefold()
    path_types = [SEGMENT_TYPES[x] for x in segments if x in SEGMENT_TYPES]
    priority = ("model", "product", "business", "place", "service", "event", "dataset",
                "person", "organization", "article", "media", "software", "brand", "resource")
    if lane == "shopping":
        return "model" if "model" in path_types else "product"
    if lane == "business":
        return "business"
    if lane == "places":
        return "place"
    if lane in ("news", "research"):
        return "article"
    for kind in priority:
        if kind in path_types:
            return kind
    return None


def _group_path(record_type: str, segments: list[str]) -> str:
    roots = ROOTS_BY_TYPE.get(record_type, set())
    for i, segment in enumerate(segments):
        if segment not in roots:
            continue
        end = i + 1
        if i + 1 < len(segments):
            nxt = segments[i + 1]
            if record_type in ("place", "business") and len(nxt) > 2:
                end = i + 2
            elif record_type in ("product", "model") and nxt in CATEGORY_SEGMENTS:
                end = i + 2
        return "/" + "/".join(segments[:end])
    return "/" + "/".join(segments[:-1]) if len(segments) > 1 else "/"


def _candidate_groups(page: dict, host: str, segments: list[str], body: str, title: str) -> list[tuple[str, str, str, list[str]]]:
    title_norm = _norm(title)
    listing = bool(LISTING_RE.search(body) or CATALOG_TITLE_RE.search(title))
    found = {}
    for i, segment in enumerate(segments):
        if segment not in CATALOG_SEGMENTS:
            continue
        mentioned = segment.replace("-", " ") in title_norm or segment in body.casefold()
        if i != len(segments) - 1 and not (mentioned and listing):
            continue
        kind = SEGMENT_TYPES[segment]
        end = i + 1
        if kind in ("place", "business") and i + 1 < len(segments) and len(segments[i + 1]) > 2:
            end = i + 2
        path = "/" + "/".join(segments[:end])
        key = host + "|" + kind + "|" + path
        evidence = ["url_segment:" + segment]
        if mentioned:
            evidence.append("page_content_mentions:" + segment)
        if listing:
            evidence.append("listing_language")
        found[key] = (key, kind, path, evidence)
    return list(found.values())


def _record_entity_key(kind: str, host: str, title: str) -> str:
    normalized = _norm(title)
    scope = host if kind in ("person", "article", "event", "resource") else ""
    return kind + "|" + scope + "|" + normalized


def _catalog_title(host: str, kind: str, path: str, source_titles: list[str]) -> str:
    explicit = [x for x in source_titles if CATALOG_TITLE_RE.search(x) or TYPE_LABEL[kind].casefold() in x.casefold()]
    if explicit:
        return sorted(explicit, key=lambda x: (len(x), x.casefold()))[0]
    token_sets = []
    for value in source_titles:
        tokens = [x for x in _norm(value).split() if len(x) > 2 and x not in STOP_TOKENS]
        if tokens:
            token_sets.append(set(tokens))
    common = set.intersection(*token_sets) if len(token_sets) >= 2 else set()
    if common:
        chosen = [x for x in source_titles[0].split() if _norm(x) in common]
        if chosen:
            subject = " ".join(chosen[:3])
            return subject + " " + TYPE_LABEL[kind]
    segment = path.strip("/").split("/")[-1] if path.strip("/") else ""
    if segment and segment not in ROOTS_BY_TYPE.get(kind, set()):
        subject = " ".join(x.capitalize() for x in segment.replace("-", " ").split())
        return subject + " " + TYPE_LABEL[kind]
    return _host_label(host) + " " + TYPE_LABEL[kind]


def analyze_pages(pages: list[dict], source_total: int | None = None, source_updated_at: str | None = None) -> dict:
    records: dict[str, dict] = {}
    record_groups: dict[str, set[str]] = defaultdict(set)
    group_pages: dict[str, set[str]] = defaultdict(set)
    group_titles: dict[str, list[str]] = defaultdict(list)
    group_urls: dict[str, set[str]] = defaultdict(set)
    explicit_groups: dict[str, dict] = {}
    explicit_page_refs: dict[str, set[str]] = defaultdict(set)
    page_rows = []
    media_urls = set()
    eligible = 0
    pages_with_signals = 0
    candidate_events = 0

    for page in pages:
        if not isinstance(page, dict):
            page_rows.append({"page_id": None, "url": None, "eligible": False, "reason": "malformed_page"})
            continue
        url = canonical_url(page.get("url"))
        if not url:
            page_rows.append({"page_id": None, "url": None, "eligible": False, "reason": "invalid_public_url"})
            continue
        page_id = stable_id("page", url)
        title = _title(page.get("title"))
        description = re.sub(r"\s+", " ", str(page.get("description") or "")).strip()[:500]
        text = re.sub(r"\s+", " ", str(page.get("text") or "")).strip()[:4000]
        body = " ".join((title, description, text))
        host = urlsplit(url).hostname or ""
        segments = _path_segments(url)
        is_eligible = page.get("robots_allowed") is not False and bool(title or description or text) and _valid_title(title)
        if is_eligible:
            eligible += 1
        page_media = {x.rstrip(".,);]") for x in MEDIA_URL_RE.findall(text)}
        media_urls.update(page_media)
        kind = _record_type(page, segments) if is_eligible else None
        group_key = host + "|" + kind + "|" + _group_path(kind, segments) if kind else None
        record_id = None
        if kind:
            entity_key = _record_entity_key(kind, host, title)
            record_id = stable_id("record", entity_key)
            item = records.setdefault(record_id, {
                "record_id": record_id, "canonical_entity_key": entity_key, "record_type": kind,
                "title": title, "description": description or text[:500],
                "source_urls": [], "source_page_ids": [], "catalog_ids": [],
                "last_seen_at": page.get("fetched_at"),
            })
            if url not in item["source_urls"]:
                item["source_urls"].append(url)
            if page_id not in item["source_page_ids"]:
                item["source_page_ids"].append(page_id)
            if page.get("fetched_at") and (not item.get("last_seen_at") or page["fetched_at"] > item["last_seen_at"]):
                item["last_seen_at"] = page["fetched_at"]
            record_groups[group_key].add(record_id)
            group_pages[group_key].add(page_id)
            group_urls[group_key].add(url)
            if title and title not in group_titles[group_key]:
                group_titles[group_key].append(title)

        explicit = _candidate_groups(page, host, segments, body, title) if is_eligible else []
        page_group_keys = []
        for key, catalog_kind, path, evidence in explicit:
            explicit_groups.setdefault(key, {
                "catalog_key": key, "catalog_type": CATALOG_TYPE[catalog_kind],
                "record_type": catalog_kind, "path": path, "evidence": set(),
            })
            explicit_groups[key]["evidence"].update(evidence)
            explicit_page_refs[key].add(page_id)
            group_pages[key].add(page_id)
            group_urls[key].add(url)
            if title and title not in group_titles[key]:
                group_titles[key].append(title)
            page_group_keys.append(key)
            candidate_events += 1
        if group_key:
            page_group_keys.append(group_key)
        if kind or explicit:
            pages_with_signals += 1
        page_rows.append({
            "page_id": page_id, "url": url, "eligible": is_eligible,
            "record_type": kind, "record_id": record_id,
            "catalog_group_keys": sorted(set(page_group_keys)),
            "media_reference_count": len(page_media),
            "fetched_at": page.get("fetched_at"),
        })

    candidate_keys = set(explicit_groups)
    for key, ids in record_groups.items():
        if len(ids) >= 2:
            candidate_keys.add(key)
            candidate_events += len(group_pages[key])

    catalogs = {}
    for key in sorted(candidate_keys):
        if key in explicit_groups:
            info = explicit_groups[key]
            kind, path = info["record_type"], info["path"]
            evidence = sorted(info["evidence"])
        else:
            _, kind, path = key.split("|", 2)
            evidence = ["multiple_distinct_records_in_same_source_category"]
        catalog_id = stable_id("catalog", key)
        catalogs[key] = {
            "catalog_id": catalog_id, "source_key": key,
            "title": _catalog_title(key.split("|", 1)[0], kind, path, group_titles.get(key, [])),
            "catalog_type": CATALOG_TYPE[kind], "record_type": kind,
            "subject": path.strip("/").split("/")[-1].replace("-", " ")[:160],
            "source_url": min(group_urls[key]) if group_urls.get(key) else "",
            "source_page_ids": sorted(group_pages.get(key, set())),
            "record_ids": sorted(record_groups.get(key, set())),
            "parent_catalog_id": None, "evidence": evidence,
            "status": "candidate_needs_source_validation",
            "verified": False,
        }

    key_to_id = {key: item["catalog_id"] for key, item in catalogs.items()}
    for key, item in catalogs.items():
        host, kind, path = key.split("|", 2)
        parent_path = "/" + "/".join(path.strip("/").split("/")[:-1]) if len(path.strip("/").split("/")) > 1 else None
        if parent_path:
            parent_key = host + "|" + kind + "|" + parent_path
            if parent_key in key_to_id:
                item["parent_catalog_id"] = key_to_id[parent_key]

    for key, ids in record_groups.items():
        catalog_id = key_to_id.get(key)
        if not catalog_id:
            continue
        for record_id in ids:
            if catalog_id not in records[record_id]["catalog_ids"]:
                records[record_id]["catalog_ids"].append(catalog_id)

    for item in records.values():
        item["source_urls"] = sorted(item["source_urls"])
        item["source_page_ids"] = sorted(item["source_page_ids"])
        item["catalog_ids"] = sorted(item["catalog_ids"])
        item["source_url"] = item["source_urls"][0] if item["source_urls"] else ""
        item["source_page_count"] = len(item["source_page_ids"])

    record_list = sorted(records.values(), key=lambda x: (x["record_type"], x["title"].casefold(), x["record_id"]))
    catalog_list = sorted(catalogs.values(), key=lambda x: (x["catalog_type"], x["title"].casefold(), x["catalog_id"]))
    type_counts = dict(sorted(Counter(x["record_type"] for x in record_list).items()))
    source_total = source_total if isinstance(source_total, int) and source_total >= 0 else len(pages)
    coverage = (len(pages) / source_total * 100.0) if source_total else 0.0
    metrics = {
        "pages_analyzed": len(pages),
        "eligible_pages": eligible,
        "unique_catalogs_discovered": len(catalog_list),
        "duplicate_catalog_candidates": max(0, candidate_events - len(catalog_list)),
        "subcatalogs_discovered": sum(bool(x["parent_catalog_id"]) for x in catalog_list),
        "unique_records": len(record_list),
        "unique_records_by_type": type_counts,
        "media_references": len(media_urls),
        "records_published": 0,
        "catalogs_verified": 0,
        "pages_with_catalog_signals": pages_with_signals,
        "coverage_percentage": round(coverage, 4),
    }
    return {
        "schema_version": 1,
        "strategy": "page-content-derived-catalog-candidates",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_index_pages": source_total,
        "source_index_updated_at": source_updated_at,
        "metrics": metrics,
        "catalogs": catalog_list,
        "records": record_list,
        "pages": page_rows,
        "media_urls": sorted(media_urls),
    }


def search_catalog_network(query: str, analysis: dict, limit: int = 10) -> list[dict]:
    tokens = [x for x in _norm(query).split() if x]
    if not tokens:
        return []
    records = {x["record_id"]: x for x in analysis.get("records", [])}
    results = []
    for catalog in analysis.get("catalogs", []):
        related = [records[x] for x in catalog.get("record_ids", []) if x in records]
        scored = []
        for record in related:
            haystack = _norm(" ".join((record.get("title", ""), record.get("description", ""), record.get("record_type", ""))))
            score = sum(3 if token in _norm(record.get("title", "")) else 1 for token in tokens if token in haystack)
            if score:
                scored.append((score, record))
        catalog_text = _norm(" ".join((catalog.get("title", ""), catalog.get("subject", ""), catalog.get("catalog_type", ""))))
        catalog_score = sum(2 for token in tokens if token in catalog_text)
        if catalog_score and not scored:
            scored = [(1, record) for record in related]
        if scored or catalog_score:
            ranked = sorted(scored, key=lambda x: (-x[0], x[1]["title"].casefold()))[:5]
            results.append({
                "catalog_id": catalog["catalog_id"], "title": catalog["title"],
                "catalog_type": catalog["catalog_type"], "status": catalog["status"],
                "score": catalog_score + sum(x[0] for x in ranked),
                "record_count": len(related),
                "matched_records": [{"record_id": x["record_id"], "title": x["title"], "record_type": x["record_type"], "source_url": x["source_url"]} for _, x in ranked],
            })
    return sorted(results, key=lambda x: (-x["score"], -x["record_count"], x["title"].casefold()))[:limit]


def _write_gzip(path: Path, payload: dict) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(compressed)
    temp.replace(path)
    return len(compressed), hashlib.sha256(compressed).hexdigest()


def write_outputs(analysis: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for catalog in analysis["catalogs"]:
        filename = hashlib.sha256(catalog["catalog_id"].encode("utf-8")).hexdigest()[:24] + ".json.gz"
        catalog["index_file"] = "indexes/" + filename
        catalog["record_count"] = len(catalog.get("record_ids", []))
        catalog["source_page_count"] = len(catalog.get("source_page_ids", []))
    size, digest = _write_gzip(output_dir / "pages.json.gz", {
        "version": 1, "source_index_pages": analysis["source_index_pages"],
        "pages": analysis["pages"],
    })
    files.append({"file": "pages.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["pages"])})
    size, digest = _write_gzip(output_dir / "registry.json.gz", {
        "version": 1, "status": "candidate_staging_not_production",
        "metrics": analysis["metrics"], "catalogs": analysis["catalogs"],
    })
    files.append({"file": "registry.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["catalogs"])})
    index_dir = output_dir / "indexes"
    records_by_catalog = defaultdict(list)
    for record in analysis["records"]:
        for catalog_id in record.get("catalog_ids", []):
            records_by_catalog[catalog_id].append(record)
    for catalog in analysis["catalogs"]:
        filename = catalog["index_file"].split("/", 1)[1]
        related = records_by_catalog.get(catalog["catalog_id"], [])
        size, digest = _write_gzip(index_dir / filename, {
            "version": 1, "catalog_id": catalog["catalog_id"],
            "title": catalog["title"], "status": catalog["status"], "records": related,
        })
        files.append({"file": "indexes/" + filename, "bytes": size, "sha256": digest, "items": len(related)})
    size, digest = _write_gzip(output_dir / "records.json.gz", {
        "version": 1, "records": analysis["records"],
    })
    files.append({"file": "records.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["records"])})
    size, digest = _write_gzip(output_dir / "media-references.json.gz", {
        "version": 1, "media_urls": analysis["media_urls"],
    })
    files.append({"file": "media-references.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["media_urls"])})
    report = {
        "schema_version": 1, "generated_at": analysis["generated_at"],
        "source_index_pages": analysis["source_index_pages"],
        "source_index_updated_at": analysis["source_index_updated_at"],
        "metrics": analysis["metrics"],
        "catalogs_verified": 0, "records_published": 0,
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "version": 1, "integrity": "sha256",
        "source_index_pages": analysis["source_index_pages"],
        "source_index_updated_at": analysis["source_index_updated_at"],
        "catalog_count": len(analysis["catalogs"]), "record_count": len(analysis["records"]),
        "files": sorted(files, key=lambda x: x["file"]),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_outputs(output_dir)
    return manifest


def validate_outputs(output_dir: Path) -> dict:
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    registry_raw = (output_dir / "registry.json.gz").read_bytes()
    registry = json.loads(gzip.decompress(registry_raw).decode("utf-8"))
    pages_raw = (output_dir / "pages.json.gz").read_bytes()
    pages = json.loads(gzip.decompress(pages_raw).decode("utf-8"))["pages"]
    records_raw = (output_dir / "records.json.gz").read_bytes()
    records = json.loads(gzip.decompress(records_raw).decode("utf-8"))["records"]
    record_ids = [x["record_id"] for x in records]
    if len(record_ids) != len(set(record_ids)) or len(records) != manifest["record_count"]:
        raise ValueError("global record registry contains duplicates or a count mismatch")
    if len(registry["catalogs"]) != manifest["catalog_count"]:
        raise ValueError("registry catalog count does not match manifest")
    if len(pages) != manifest["source_index_pages"]:
        raise ValueError("page-analysis coverage does not match source index page count")
    catalog_ids = [x["catalog_id"] for x in registry["catalogs"]]
    listed_index_files = {x["file"] for x in manifest["files"]}
    if any(x.get("index_file") not in listed_index_files for x in registry["catalogs"]):
        raise ValueError("registry references a missing per-catalog index")
    if len(catalog_ids) != len(set(catalog_ids)):
        raise ValueError("duplicate catalog IDs remain after deduplication")
    for entry in manifest["files"]:
        path = output_dir / entry["file"]
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("catalog analysis artifact integrity mismatch: " + entry["file"])
        payload = json.loads(gzip.decompress(raw).decode("utf-8"))
        if entry["file"].startswith("indexes/"):
            if payload.get("catalog_id") not in set(catalog_ids):
                raise ValueError("orphan per-catalog index detected")
            indexed_ids = [x["record_id"] for x in payload.get("records", [])]
            if len(indexed_ids) != len(set(indexed_ids)) or any(x not in set(record_ids) for x in indexed_ids):
                raise ValueError("per-catalog index contains duplicate or unknown record IDs")
    if registry["status"] != "candidate_staging_not_production":
        raise ValueError("candidate registry must not be advertised as production")
    return {"catalogs": len(catalog_ids), "pages_analyzed": len(pages), "files_validated": len(manifest["files"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        print(json.dumps(validate_outputs(args.output_dir), indent=2))
        return
    source_manifest = json.loads(args.index.read_text(encoding="utf-8"))
    index = load_index(args.index)
    analysis = analyze_pages(
        index.get("pages", []),
        source_total=source_manifest.get("total_pages"),
        source_updated_at=datetime.fromtimestamp(source_manifest.get("updated_at", 0), timezone.utc).isoformat().replace("+00:00", "Z") if source_manifest.get("updated_at") else None,
    )
    manifest = write_outputs(analysis, args.output_dir)
    print(json.dumps({"metrics": analysis["metrics"], "catalog_index_files": manifest["catalog_count"], "files": len(manifest["files"])}, indent=2))
    for query in (args.query or ["Rolex", "Las Vegas"]):
        print(json.dumps({"query": query, "results": search_catalog_network(query, analysis)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
