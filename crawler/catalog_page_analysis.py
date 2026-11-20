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
ANALYZER_VERSION = 3
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "ref", "source"}
STOP_TOKENS = {
    "the", "and", "for", "with", "from", "our", "your", "all", "new", "home", "page",
    "official", "welcome", "lock", "menu", "search", "skip", "content", "login", "sign",
    "subscribe", "close", "cookie", "accept", "privacy", "terms", "policy", "back", "next",
    "previous", "print", "share",
}
GENERIC_TITLES = {"home", "homepage", "welcome", "404", "not found", "access denied", "page not found"}
MEDIA_URL_RE = re.compile(r"https?://[^\s<>\"']+\.(?:jpg|jpeg|png|webp|gif|svg|mp4|webm|mov|mp3|wav|m4a)(?:\?[^\s<>\"']*)?", re.I)
LISTING_RE = re.compile(
    r"\b(?:browse(?:\s+all)?|view\s+all|all\s+(?:products|models|services|locations|places|events|datasets|articles|brands)|filter\s+by|sort\s+by|search\s+results|showing\s+\d+|catalog(?:ue)?|store\s+locator|find\s+(?:a\s+)?(?:location|business|place)|business\s+directory|places\s+directory)\b",
    re.I,
)
CATALOG_TITLE_RE = re.compile(
    r"\b(?:catalog(?:ue)?|all\s+(?:products|models|services|locations|places|events|datasets|articles|brands)|browse\s+all|shop\s+all|store\s+locator|product\s+listing|model\s+listing|business\s+directory|places\s+directory|service\s+directory)\b",
    re.I,
)

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
CONTENT_INTENT_TOKENS = {
    "news", "article", "articles", "story", "stories", "research", "paper", "papers",
    "report", "reports", "media", "image", "images", "video", "videos", "resource", "resources",
    "subject", "subjects", "topic", "topics",
}
SEGMENT_TYPES.update({
    "subjects": "subject", "subject": "subject", "topics": "subject",
    "topic": "subject", "themes": "subject", "theme": "subject",
})
CATALOG_SEGMENTS.update({"subjects", "subject", "topics", "topic", "themes", "theme"})
ROOTS_BY_TYPE["subject"] = {"subjects", "subject", "topics", "topic", "themes", "theme"}
CATEGORY_SEGMENTS.update({"subjects", "topics", "themes"})
CATALOG_TYPE["subject"] = "knowledge_base"
TYPE_LABEL["subject"] = "Subjects"

LIST_ENTITY_PATTERNS = (
    ("product", "products", re.compile(r"\b(?:products?|items?)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("model", "models", re.compile(r"\bmodels?\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("brand", "brands", re.compile(r"\bbrands?\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("service", "services", re.compile(r"\bservices?\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("business", "businesses", re.compile(r"\b(?:businesses|companies|stores|dealers|vendors)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("place", "places", re.compile(r"\b(?:places|locations|attractions|destinations|parks|museums)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("person", "people", re.compile(r"\b(?:people|authors|speakers|artists|team members)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("organization", "organizations", re.compile(r"\b(?:organizations|agencies|institutions|partners)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("event", "events", re.compile(r"\b(?:events|conferences|exhibitions)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("dataset", "datasets", re.compile(r"\b(?:datasets|data sets|registries)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("article", "articles", re.compile(r"\b(?:articles|publications|reports|papers)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("software", "software", re.compile(r"\b(?:software|tools|packages|libraries)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
    ("subject", "subjects", re.compile(r"\b(?:subjects|topics|themes)\s+(?:include|such as|like|:)\s+([^.!?\n]{3,260})", re.I)),
)
DEFAULT_CATALOG_PATHS = {
    "product": "/products", "model": "/models", "brand": "/brands",
    "service": "/services", "business": "/businesses", "place": "/places",
    "person": "/people", "organization": "/organizations", "event": "/events",
    "dataset": "/datasets", "article": "/articles", "media": "/media",
    "software": "/software", "resource": "/resources", "subject": "/subjects",
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


def _dedupe_source_pages(pages: list[dict]) -> tuple[list[dict], int]:
    """Deduplicate canonical source URLs; any robots denial wins conservatively."""
    by_url: dict[str, dict] = {}
    invalid_pages = []
    duplicate_rows = 0

    def quality(page: dict) -> tuple[int, int]:
        content_size = sum(len(str(page.get(key) or "")) for key in ("title", "description", "text"))
        try:
            fetched_at = int(page.get("fetched_at") or 0)
        except (TypeError, ValueError):
            fetched_at = 0
        return content_size, fetched_at

    for page in pages:
        if not isinstance(page, dict):
            invalid_pages.append(page)
            continue
        url = canonical_url(page.get("url"))
        if not url:
            invalid_pages.append(page)
            continue
        current = by_url.get(url)
        if current is None:
            by_url[url] = dict(page)
            continue
        duplicate_rows += 1
        candidates = (current, page)
        winner = dict(max(candidates, key=quality))
        if any(candidate.get("robots_allowed") is False for candidate in candidates):
            winner["robots_allowed"] = False
        timestamps = []
        for candidate in candidates:
            try:
                timestamps.append(int(candidate.get("fetched_at") or 0))
            except (TypeError, ValueError):
                pass
        if timestamps:
            winner["fetched_at"] = max(timestamps)
        by_url[url] = winner

    deduplicated = [by_url[url] for url in sorted(by_url)]
    deduplicated.extend(invalid_pages)
    return deduplicated, duplicate_rows


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
                "person", "organization", "subject", "article", "media", "software", "brand", "resource")
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
    # Without a verified global identifier, host scope prevents false merges
    # such as unrelated products or businesses sharing a generic display title.
    return kind + "|" + host + "|" + normalized


def _content_group_path(record_type: str, segments: list[str]) -> str:
    if not any(segment in ROOTS_BY_TYPE.get(record_type, set()) for segment in segments):
        return DEFAULT_CATALOG_PATHS[record_type]
    return _group_path(record_type, segments)


def _extract_list_entities(body: str) -> list[tuple[str, str, str]]:
    """Extract only explicitly enumerated, title-cased entities from page text."""
    found = {}
    for record_type, cue, pattern in LIST_ENTITY_PATTERNS:
        for match in pattern.finditer(body):
            parts = re.split(r"\s*[,;•]\s*|\s+\band\b\s+|\s+\bor\b\s+", match.group(1), flags=re.I)
            for part in parts:
                name = re.sub(r"^(?:the|a|an)\s+", "", part.strip(), flags=re.I)
                name = re.sub(r"\s+(?:etc\.?|and more|among others|and others)$", "", name, flags=re.I)
                name = name.strip(" \t:-–—'\"()[]{}")
                normalized = _norm(name)
                if not 3 <= len(name) <= 100 or not re.search(r"[A-Z0-9]", name):
                    continue
                if normalized in {"more", "other", "others", "various", "etc"}:
                    continue
                found[(record_type, normalized)] = (record_type, name, cue)
                if len(found) >= 30:
                    return list(found.values())
    return list(found.values())


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


def analyze_pages(
    pages: list[dict],
    source_total: int | None = None,
    source_updated_at: str | None = None,
    source_index_sha256: str | None = None,
) -> dict:
    source_page_rows = len(pages)
    pages, duplicate_source_page_rows = _dedupe_source_pages(pages)
    records: dict[str, dict] = {}
    record_groups: dict[str, set[str]] = defaultdict(set)
    group_pages: dict[str, set[str]] = defaultdict(set)
    group_titles: dict[str, list[str]] = defaultdict(list)
    group_urls: dict[str, set[str]] = defaultdict(set)
    explicit_groups: dict[str, dict] = {}
    page_rows = []
    media_urls = set()
    media_references = {}
    eligible = 0
    pages_with_signals = 0
    content_entity_mentions = 0

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
        page_media = ({
            normalized for raw_media in MEDIA_URL_RE.findall(text)
            if (normalized := canonical_url(raw_media.rstrip(".,);]")))
        } if is_eligible else set())
        media_urls.update(page_media)
        for media_url in page_media:
            media_references[(page_id, media_url)] = {
                "media_id": stable_id("media", media_url),
                "media_url": media_url,
                "source_page_id": page_id,
                "source_url": url,
                "discovered_at": page.get("fetched_at"),
            }
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
        content_entities = _extract_list_entities(body) if is_eligible else []
        content_entity_mentions += len(content_entities)
        page_group_keys = []
        for key, catalog_kind, path, evidence in explicit:
            explicit_groups.setdefault(key, {
                "catalog_key": key, "catalog_type": CATALOG_TYPE[catalog_kind],
                "record_type": catalog_kind, "path": path, "evidence": set(),
            })
            explicit_groups[key]["evidence"].update(evidence)
            group_pages[key].add(page_id)
            group_urls[key].add(url)
            if title and title not in group_titles[key]:
                group_titles[key].append(title)
            page_group_keys.append(key)
        for entity_kind, entity_title, cue in content_entities:
            entity_key = _record_entity_key(entity_kind, host, entity_title)
            entity_id = stable_id("record", entity_key)
            item = records.setdefault(entity_id, {
                "record_id": entity_id, "canonical_entity_key": entity_key, "record_type": entity_kind,
                "title": entity_title,
                "description": f"Explicit {cue} list item extracted from {title or host}.",
                "source_urls": [], "source_page_ids": [], "catalog_ids": [],
                "last_seen_at": page.get("fetched_at"), "content_extracted": True,
            })
            item["content_extracted"] = True
            if url not in item["source_urls"]:
                item["source_urls"].append(url)
            if page_id not in item["source_page_ids"]:
                item["source_page_ids"].append(page_id)
            if page.get("fetched_at") and (not item.get("last_seen_at") or page["fetched_at"] > item["last_seen_at"]):
                item["last_seen_at"] = page["fetched_at"]
            content_path = _content_group_path(entity_kind, segments)
            content_key = host + "|" + entity_kind + "|" + content_path
            record_groups[content_key].add(entity_id)
            group_pages[content_key].add(page_id)
            group_urls[content_key].add(url)
            if title and title not in group_titles[content_key]:
                group_titles[content_key].append(title)
            page_group_keys.append(content_key)
        if group_key:
            page_group_keys.append(group_key)
        if kind or explicit or content_entities:
            pages_with_signals += 1
        page_rows.append({
            "page_id": page_id, "url": url, "eligible": is_eligible,
            "record_type": kind, "record_id": record_id,
            "catalog_group_keys": sorted(set(page_group_keys)),
            "media_reference_count": len(page_media),
            "content_entity_count": len(content_entities),
            "fetched_at": page.get("fetched_at"),
        })

    candidate_keys = set(explicit_groups)
    for key, ids in record_groups.items():
        if len(ids) >= 2:
            candidate_keys.add(key)

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
    source_total = source_total if isinstance(source_total, int) and source_total >= 0 else source_page_rows
    unique_source_total = max(len(pages), source_total - duplicate_source_page_rows)
    coverage = (len(pages) / unique_source_total * 100.0) if unique_source_total else 0.0
    metrics = {
        "pages_analyzed": len(pages),
        "source_page_rows": source_page_rows,
        "unique_source_pages": len(pages),
        "duplicate_source_page_rows": duplicate_source_page_rows,
        "eligible_pages": eligible,
        "unique_catalogs_discovered": len(catalog_list),
        "catalog_candidate_page_references": sum(len(x.get("source_page_ids", [])) for x in catalog_list),
        "duplicate_catalog_candidates": max(
            0,
            sum(len(x.get("source_page_ids", [])) for x in catalog_list) - len(catalog_list),
        ),
        "subcatalogs_discovered": sum(bool(x["parent_catalog_id"]) for x in catalog_list),
        "unique_records": len(record_list),
        "unique_records_by_type": type_counts,
        "content_entity_mentions": content_entity_mentions,
        "content_extracted_unique_records": sum(1 for item in record_list if item.get("content_extracted")),
        "media_references": len(media_references),
        "unique_media_urls": len(media_urls),
        "records_published": 0,
        "catalogs_verified": 0,
        "pages_with_catalog_signals": pages_with_signals,
        "coverage_percentage": round(coverage, 4),
        "eligible_page_coverage_percentage": round((eligible / unique_source_total * 100.0) if unique_source_total else 0.0, 4),
    }
    return {
        "schema_version": 1,
        "analysis_version": ANALYZER_VERSION,
        "strategy": "page-content-derived-catalog-candidates",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_index_pages": source_total,
        "source_index_updated_at": source_updated_at,
        "source_index_sha256": source_index_sha256,
        "metrics": metrics,
        "catalogs": catalog_list,
        "records": record_list,
        "pages": page_rows,
        "media_urls": sorted(media_urls),
        "media_references": sorted(media_references.values(), key=lambda x: (x["media_url"], x["source_page_id"])),
    }


def analyze_pages_incremental(
    pages: list[dict],
    source_total: int | None = None,
    source_updated_at: str | None = None,
    source_index_sha256: str | None = None,
    cache_dir: Path | None = None,
) -> dict:
    """Reuse per-host analysis shards when that host's indexed pages are unchanged.

    Catalog and entity IDs are host-scoped, so host shards can be safely merged.
    """
    source_page_rows = len(pages)
    pages, duplicate_source_page_rows = _dedupe_source_pages(pages)
    cache_dir = cache_dir or (DEFAULT_OUTPUT.parent / ".page-derived-catalog-cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for page in pages:
        url = canonical_url(page.get("url")) if isinstance(page, dict) else None
        host = urlsplit(url).hostname if url else None
        grouped[host or "_invalid_public_url"].append(page)
    all_catalogs, all_records, all_page_rows = [], [], []
    all_media: set[str] = set()
    all_media_references = []
    content_entity_mentions = 0
    eligible = signals = 0
    reused = rebuilt = 0
    for host, host_pages in sorted(grouped.items()):
        payload = json.dumps(host_pages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        cache_path = cache_dir / (hashlib.sha256(host.encode("utf-8")).hexdigest()[:32] + ".json.gz")
        shard = None
        if cache_path.exists():
            try:
                cached = json.loads(gzip.decompress(cache_path.read_bytes()).decode("utf-8"))
                if cached.get("analyzer_version") == ANALYZER_VERSION and cached.get("input_sha256") == digest:
                    shard = cached.get("analysis")
                    reused += 1
            except (OSError, ValueError, json.JSONDecodeError):
                shard = None
        if shard is None:
            shard = analyze_pages(host_pages, source_total=len(host_pages))
            raw = json.dumps({"analyzer_version": ANALYZER_VERSION, "input_sha256": digest, "analysis": shard}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            temp = cache_path.with_suffix(".tmp")
            temp.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
            temp.replace(cache_path)
            rebuilt += 1
        all_catalogs.extend(shard["catalogs"])
        all_records.extend(shard["records"])
        all_page_rows.extend(shard["pages"])
        all_media.update(shard["media_urls"])
        all_media_references.extend(shard.get("media_references", []))
        content_entity_mentions += shard["metrics"].get("content_entity_mentions", 0)
        eligible += shard["metrics"]["eligible_pages"]
        signals += shard["metrics"]["pages_with_catalog_signals"]
    all_catalogs.sort(key=lambda x: (x["catalog_type"], x["title"].casefold(), x["catalog_id"]))
    all_records.sort(key=lambda x: (x["record_type"], x["title"].casefold(), x["record_id"]))
    all_page_rows.sort(key=lambda x: (x.get("url") or "", x.get("page_id") or ""))
    source_total = source_total if isinstance(source_total, int) and source_total >= 0 else source_page_rows
    unique_source_total = max(len(pages), source_total - duplicate_source_page_rows)
    page_refs = sum(len(x.get("source_page_ids", [])) for x in all_catalogs)
    type_counts = dict(sorted(Counter(x["record_type"] for x in all_records).items()))
    metrics = {
        "pages_analyzed": len(pages),
        "source_page_rows": source_page_rows,
        "unique_source_pages": len(pages),
        "duplicate_source_page_rows": duplicate_source_page_rows,
        "eligible_pages": eligible,
        "unique_catalogs_discovered": len(all_catalogs),
        "catalog_candidate_page_references": page_refs,
        "duplicate_catalog_candidates": max(0, page_refs - len(all_catalogs)),
        "subcatalogs_discovered": sum(bool(x["parent_catalog_id"]) for x in all_catalogs),
        "unique_records": len(all_records), "unique_records_by_type": type_counts,
        "content_entity_mentions": content_entity_mentions,
        "content_extracted_unique_records": sum(1 for item in all_records if item.get("content_extracted")),
        "media_references": len({(x["source_page_id"], x["media_url"]) for x in all_media_references}),
        "unique_media_urls": len(all_media), "records_published": 0, "catalogs_verified": 0,
        "pages_with_catalog_signals": signals,
        "coverage_percentage": round((len(pages) / unique_source_total * 100.0) if unique_source_total else 0.0, 4),
        "eligible_page_coverage_percentage": round((eligible / unique_source_total * 100.0) if unique_source_total else 0.0, 4),
        "host_partitions_reused": reused, "host_partitions_rebuilt": rebuilt,
    }
    return {
        "schema_version": 1, "analysis_version": ANALYZER_VERSION,
        "strategy": "incremental-host-partitioned-page-content-derived-catalog-candidates",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_index_pages": source_total, "source_index_updated_at": source_updated_at,
        "source_index_sha256": source_index_sha256, "metrics": metrics,
        "catalogs": all_catalogs, "records": all_records, "pages": all_page_rows,
        "media_urls": sorted(all_media),
        "media_references": sorted(
            {(x["source_page_id"], x["media_url"]): x for x in all_media_references}.values(),
            key=lambda x: (x["media_url"], x["source_page_id"]),
        ),
    }


def search_catalog_network(query: str, analysis: dict, limit: int = 10) -> list[dict]:
    tokens = list(dict.fromkeys(_norm(query).split()))
    if not tokens or limit <= 0:
        return []
    token_set = set(tokens)
    query_phrase = " ".join(tokens)
    records = {x["record_id"]: x for x in analysis.get("records", [])}
    results = []
    for catalog in analysis.get("catalogs", []):
        related = [records[x] for x in catalog.get("record_ids", []) if x in records]
        catalog_text = _norm(" ".join((
            catalog.get("title", ""), catalog.get("subject", ""),
        )))
        catalog_tokens = set(catalog_text.split())
        catalog_match = token_set.issubset(catalog_tokens)
        record_type = str(catalog.get("record_type", ""))
        # Article/resource/media catalogs are searchable only when the query
        # explicitly asks for that content type; a city or brand name alone
        # must not surface an unrelated news publisher's generic archive.
        if record_type in ("article", "resource", "media", "subject") and not (token_set & CONTENT_INTENT_TOKENS):
            continue
        matched = []
        for record in related:
            title_norm = _norm(record.get("title", ""))
            title_tokens = set(title_norm.split())
            description_tokens = set(_norm(record.get("description", "")).split())
            combined_tokens = catalog_tokens | title_tokens
            if record_type not in ("article", "resource", "media"):
                combined_tokens |= description_tokens
            if not token_set.issubset(combined_tokens):
                continue
            score = 20
            if token_set.issubset(title_tokens):
                score += 40
            if query_phrase and query_phrase in title_norm:
                score += 10
            score += 2 * len(token_set & description_tokens)
            matched.append((score, record))
        if not catalog_match and not matched:
            continue
        elif not matched:
            matched = [(0, record) for record in related]
        catalog_score = 100 + (10 * len(tokens)) if catalog_match else 0
        type_bonus = 30 if len(tokens) >= 2 and catalog.get("catalog_type") == "geographic" else 0
        ranked = sorted(matched, key=lambda x: (-x[0], x[1]["title"].casefold()))[:5]
        results.append({
            "catalog_id": catalog["catalog_id"],
            "title": catalog["title"],
            "catalog_type": catalog["catalog_type"],
            "status": catalog["status"],
            "match_scope": "catalog" if catalog_match else "records",
            "score": catalog_score + type_bonus + sum(x[0] for x in ranked),
            "record_count": len(related),
            "matched_records": [{
                "record_id": x["record_id"],
                "title": x["title"],
                "record_type": x["record_type"],
                "source_url": x["source_url"],
            } for _, x in ranked],
        })
    return sorted(
        results,
        key=lambda x: (-x["score"], -len(x["matched_records"]), x["title"].casefold()),
    )[:limit]


def search_source_page_evidence(query: str, pages: list[dict], limit: int = 5) -> list[dict]:
    """Return bounded source-page evidence without promoting snippets to catalogs."""
    tokens = set(_norm(query).split())
    if not tokens or limit <= 0:
        return []
    matches = []
    for page in pages:
        if not isinstance(page, dict) or page.get("robots_allowed") is False:
            continue
        url = canonical_url(page.get("url"))
        if not url:
            continue
        title = _title(page.get("title"))
        description = re.sub(r"\s+", " ", str(page.get("description") or "")).strip()
        body = re.sub(r"\s+", " ", str(page.get("text") or "")).strip()
        combined = set(_norm(" ".join((title, description, body))).split())
        if not tokens.issubset(combined):
            continue
        title_tokens = set(_norm(title).split())
        score = 20 + (30 if tokens.issubset(title_tokens) else 0)
        score += 3 * len(tokens & set(_norm(description).split()))
        matches.append({
            "url": url, "title": title, "lane": page.get("lane"),
            "matched_in_title": tokens.issubset(title_tokens),
            "snippet": (description or body)[:280],
            "score": score,
        })
    matches.sort(key=lambda x: (-x["score"], x["title"].casefold(), x["url"]))
    return matches[:limit]


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
        "version": 1, "analysis_version": analysis.get("analysis_version", ANALYZER_VERSION),
        "status": "candidate_staging_not_production",
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
        "version": 2, "references": analysis["media_references"],
        "unique_media_urls": analysis["media_urls"],
    })
    files.append({"file": "media-references.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["media_references"])})
    report = {
        "schema_version": 1, "analysis_version": analysis.get("analysis_version", ANALYZER_VERSION),
        "generated_at": analysis["generated_at"],
        "source_index_pages": analysis["source_index_pages"],
        "source_index_updated_at": analysis["source_index_updated_at"],
        "source_index_sha256": analysis.get("source_index_sha256"),
        "metrics": analysis["metrics"],
        "catalogs_verified": 0, "records_published": 0,
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "version": 1, "analysis_version": analysis.get("analysis_version", ANALYZER_VERSION),
        "integrity": "sha256",
        "source_index_pages": analysis["source_index_pages"],
        "source_index_updated_at": analysis["source_index_updated_at"],
        "source_index_sha256": analysis.get("source_index_sha256"),
        "catalog_count": len(analysis["catalogs"]), "record_count": len(analysis["records"]),
        "files": sorted(files, key=lambda x: x["file"]),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_outputs(output_dir)
    return manifest


def validate_outputs(output_dir: Path) -> dict:
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    source_index_sha256 = manifest.get("source_index_sha256")
    if source_index_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(source_index_sha256)):
        raise ValueError("source index SHA-256 is malformed")
    registry_raw = (output_dir / "registry.json.gz").read_bytes()
    registry = json.loads(gzip.decompress(registry_raw).decode("utf-8"))
    pages_raw = (output_dir / "pages.json.gz").read_bytes()
    pages = json.loads(gzip.decompress(pages_raw).decode("utf-8"))["pages"]
    page_by_id = {item["page_id"]: item for item in pages if item.get("page_id")}
    media_payload = json.loads(gzip.decompress((output_dir / "media-references.json.gz").read_bytes()).decode("utf-8"))
    media_references = media_payload.get("references", [])
    media_pairs = [(x.get("source_page_id"), x.get("media_url")) for x in media_references]
    if len(media_pairs) != len(set(media_pairs)):
        raise ValueError("media references contain duplicate page/media pairs")
    for reference in media_references:
        source_page = page_by_id.get(reference.get("source_page_id"))
        media_url = reference.get("media_url", "")
        if (
            source_page is None
            or reference.get("source_url") != source_page.get("url")
            or canonical_url(media_url) != media_url
            or stable_id("media", media_url) != reference.get("media_id")
        ):
            raise ValueError("media reference provenance or stable ID is invalid")
    unique_media_urls = sorted({x.get("media_url") for x in media_references})
    if unique_media_urls != media_payload.get("unique_media_urls", []):
        raise ValueError("media URL registry does not match its provenance references")
    records_raw = (output_dir / "records.json.gz").read_bytes()
    records = json.loads(gzip.decompress(records_raw).decode("utf-8"))["records"]
    page_ids = {
        item["page_id"] for item in pages
        if isinstance(item, dict) and item.get("page_id")
    }
    record_ids = [x["record_id"] for x in records]
    records_by_id = {x["record_id"]: x for x in records}
    if len(record_ids) != len(set(record_ids)) or len(records) != manifest["record_count"]:
        raise ValueError("global record registry contains duplicates or a count mismatch")
    for record in records:
        if stable_id("record", record.get("canonical_entity_key", "")) != record.get("record_id"):
            raise ValueError("record ID does not match its canonical entity key")
        if not set(record.get("source_page_ids", [])).issubset(page_ids):
            raise ValueError("record provenance references an unknown source page")
        source_urls = record.get("source_urls", [])
        if not source_urls or any(canonical_url(url) != url for url in source_urls):
            raise ValueError("record source URL is missing or not canonical")
    catalogs = registry["catalogs"]
    if len(catalogs) != manifest["catalog_count"]:
        raise ValueError("registry catalog count does not match manifest")
    catalog_ids = [x["catalog_id"] for x in catalogs]
    catalogs_by_id = {x["catalog_id"]: x for x in catalogs}
    if len(catalog_ids) != len(set(catalog_ids)):
        raise ValueError("duplicate catalog IDs remain after deduplication")
    for catalog in catalogs:
        if stable_id("catalog", catalog.get("source_key", "")) != catalog.get("catalog_id"):
            raise ValueError("catalog ID does not match its canonical source key")
        if not set(catalog.get("source_page_ids", [])).issubset(page_ids):
            raise ValueError("catalog provenance references an unknown source page")
        related_ids = catalog.get("record_ids", [])
        if len(related_ids) != len(set(related_ids)) or any(x not in records_by_id for x in related_ids):
            raise ValueError("catalog references duplicate or unknown record IDs")
        parent_id = catalog.get("parent_catalog_id")
        if parent_id is not None and parent_id not in catalogs_by_id:
            raise ValueError("catalog references an unknown parent catalog")
    listed_index_files = [x["file"] for x in manifest["files"]]
    if len(listed_index_files) != len(set(listed_index_files)):
        raise ValueError("catalog manifest contains duplicate file paths")
    if any(x.get("index_file") not in set(listed_index_files) for x in catalogs):
        raise ValueError("registry references a missing per-catalog index")
    index_catalog_ids = set()
    for entry in manifest["files"]:
        path = output_dir / entry["file"]
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("catalog analysis artifact integrity mismatch: " + entry["file"])
        payload = json.loads(gzip.decompress(raw).decode("utf-8"))
        if entry["file"].startswith("indexes/"):
            catalog_id = payload.get("catalog_id")
            if catalog_id not in catalogs_by_id or catalog_id in index_catalog_ids:
                raise ValueError("orphan or duplicate per-catalog index detected")
            index_catalog_ids.add(catalog_id)
            indexed_ids = [x["record_id"] for x in payload.get("records", [])]
            expected_ids = set(catalogs_by_id[catalog_id].get("record_ids", []))
            if len(indexed_ids) != len(set(indexed_ids)) or set(indexed_ids) != expected_ids:
                raise ValueError("per-catalog index does not exactly match registry record membership")
            if any(catalog_id not in records_by_id[x].get("catalog_ids", []) for x in indexed_ids):
                raise ValueError("per-catalog index record is missing its catalog relationship")
    if index_catalog_ids != set(catalog_ids):
        raise ValueError("one or more catalogs are missing a per-catalog index")
    if registry["status"] != "candidate_staging_not_production":
        raise ValueError("candidate registry must not be advertised as production")
    return {
        "catalogs": len(catalog_ids),
        "pages_analyzed": len(pages),
        "records_validated": len(records),
        "media_references_validated": len(media_references),
        "unique_media_urls": len(unique_media_urls),
        "files_validated": len(manifest["files"]),
        "per_catalog_indexes_validated": len(index_catalog_ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-incremental", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / ".page-derived-catalog-cache")
    args = parser.parse_args()
    if args.validate_only:
        print(json.dumps(validate_outputs(args.output_dir), indent=2))
        return
    source_manifest = json.loads(args.index.read_text(encoding="utf-8"))
    index = load_index(args.index)
    analyze = analyze_pages if args.no_incremental else analyze_pages_incremental
    analysis = analyze(
        index.get("pages", []),
        source_total=source_manifest.get("total_pages"),
        source_updated_at=datetime.fromtimestamp(source_manifest.get("updated_at", 0), timezone.utc).isoformat().replace("+00:00", "Z") if source_manifest.get("updated_at") else None,
        source_index_sha256=hashlib.sha256(args.index.read_bytes()).hexdigest(),
        **({} if args.no_incremental else {"cache_dir": args.cache_dir}),
    )
    manifest = write_outputs(analysis, args.output_dir)
    print(json.dumps({"metrics": analysis["metrics"], "catalog_index_files": manifest["catalog_count"], "files": len(manifest["files"])}, indent=2))
    for query in (args.query or ["Rolex", "Las Vegas"]):
        print(json.dumps({
            "query": query,
            "results": search_catalog_network(query, analysis),
            "source_page_evidence": search_source_page_evidence(query, index.get("pages", []), limit=5),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
