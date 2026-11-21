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
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "data" / "index.json"
DEFAULT_OUTPUT = ROOT / "data" / "page-derived-catalogs"
DEFAULT_SOURCE_POLICY_REVIEWS = ROOT / "data" / "source-policy-reviews.json"
ANALYZER_VERSION = 12
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
    r"\b(?:browse(?:\s+all)?|view\s+all|all\s+(?:products|models|services|locations|places|events|datasets|articles|brands|offices)|filter\s+by|sort\s+by|search\s+results|showing\s+\d+|catalog(?:ue)?\s+(?:of|for)|(?:product|model|service|business|place|event|dataset|article|brand)\s+catalog(?:ue)?|store\s+locator|find\s+(?:a\s+)?(?:location|business|place|office)|business\s+directory|places\s+directory|office\s+directory)\b",
    re.I,
)
CATALOG_TITLE_RE = re.compile(
    r"\b(?:catalog(?:ue)?\s+(?:of|for)|(?:product|model|service|business|place|event|dataset|article|brand)\s+catalog(?:ue)?|all\s+(?:products|models|services|locations|places|events|datasets|articles|brands|offices)|browse\s+all|shop\s+all|store\s+locator|product\s+listing|model\s+listing|business\s+directory|places\s+directory|service\s+directory|office\s+directory)\b",
    re.I,
)

TITLE_KIND_PATTERNS = {
    "product": re.compile(r"\b(?:products?|items?)\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+(?:products?|items?)\b|\b(?:all|browse(?:\s+all)?|shop(?:\s+all)?)\s+(?:products?|items?)\b", re.I),
    "model": re.compile(r"\bmodels?\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+models?\b|\b(?:all|browse(?:\s+all)?)\s+models?\b|\bwatches?\s+(?:catalog(?:ue)?|models?)\b", re.I),
    "brand": re.compile(r"\bbrands?\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+brands?\b|\b(?:all|browse(?:\s+all)?)\s+brands?\b", re.I),
    "service": re.compile(r"\bservices?\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+services?\b|\bservice\s+directory\b", re.I),
    "business": re.compile(r"\b(?:businesses|companies|stores|dealers|vendors)\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+(?:businesses|companies|stores|dealers|vendors)\b|\bstore\s+locator\b", re.I),
    "place": re.compile(r"\b(?:places|locations|attractions|destinations|parks|museums)\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+(?:places|locations|attractions|destinations|parks|museums)\b", re.I),
    "person": re.compile(r"\b(?:people|authors|speakers|artists)\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:people|staff|team)\s+directory\b", re.I),
    "organization": re.compile(r"\b(?:organizations|agencies|institutions|offices)\s+(?:catalog(?:ue)?|listing|directory)\b|\b(?:organization|agency|office)\s+directory\b", re.I),
    "event": re.compile(r"\b(?:events|conferences|exhibitions)\s+(?:catalog(?:ue)?|listing|directory|calendar)\b|\b(?:event|conference)\s+calendar\b", re.I),
    "dataset": re.compile(r"\b(?:datasets?|data\s+sets?|registries)\s+(?:catalog(?:ue)?|listing|directory|registry)\b|\b(?:catalog(?:ue)?|listing|directory)\s+(?:of|for)\s+(?:datasets?|data\s+sets?|registries)\b", re.I),
    "article": re.compile(r"\b(?:articles?|news|publications?|reports?|papers?)\s+(?:catalog(?:ue)?|listing|directory|archive|library)\b|\b(?:article|news|publication|research)\s+(?:archive|library|directory)\b", re.I),
    "media": re.compile(r"\b(?:media|images?|videos?|audio)\s+(?:catalog(?:ue)?|gallery|listing|directory|library)\b|\b(?:image|video|media)\s+gallery\b", re.I),
    "software": re.compile(r"\b(?:software|packages?|tools?|libraries)\s+(?:catalog(?:ue)?|listing|directory|registry)\b|\bsoftware\s+directory\b", re.I),
    "resource": re.compile(r"\bresources?\s+(?:catalog(?:ue)?|listing|directory|library)\b|\bresource\s+library\b", re.I),
}



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
    "organizations": "organization", "organization": "organization", "offices": "organization", "office": "organization", "agencies": "organization",
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
    "organization": {"organizations", "organization", "offices", "office", "agencies", "agency", "institutions", "foundation"},
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


def _timestamp_iso(value):
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
            if record_type in ("place", "business", "product", "model") and nxt in CATEGORY_SEGMENTS:
                end = i + 2
        return "/" + "/".join(segments[:end])
    return "/" + "/".join(segments[:-1]) if len(segments) > 1 else "/"


def _candidate_groups(page: dict, host: str, segments: list[str], body: str, title: str) -> list[tuple[str, str, str, list[str]]]:
    title_is_catalog = bool(CATALOG_TITLE_RE.search(title))
    listing = bool(LISTING_RE.search(body) or title_is_catalog)
    found = {}
    for i, segment in enumerate(segments):
        if segment not in CATALOG_SEGMENTS:
            continue
        mentioned = segment.replace("-", " ") in _norm(title) or segment in body.casefold()
        if i != len(segments) - 1 and not (mentioned and listing):
            continue
        kind = SEGMENT_TYPES[segment]
        end = i + 1
        if kind in ("place", "business", "product", "model") and i + 1 < len(segments) and segments[i + 1] in CATEGORY_SEGMENTS:
            end = i + 2
        path = "/" + "/".join(segments[:end])
        key = host + "|" + kind + "|" + path
        evidence = ["url_segment:" + segment]
        if mentioned:
            evidence.append("page_content_mentions:" + segment)
        if listing:
            evidence.append("listing_language")
        found[key] = (key, kind, path, evidence)

    # A catalog title can define a collection even when the URL is /catalog,
    # /directory, or /about rather than a typed path such as /products.
    if title_is_catalog:
        for kind, pattern in TITLE_KIND_PATTERNS.items():
            if not pattern.search(title):
                continue
            roots = ROOTS_BY_TYPE.get(kind, set())
            path = (
                _group_path(kind, segments)
                if any(segment in roots for segment in segments)
                else DEFAULT_CATALOG_PATHS[kind]
            )
            key = host + "|" + kind + "|" + path
            found[key] = (key, kind, path, ["catalog_title", "listing_language", "title_kind:" + kind])
    return list(found.values())

def _record_entity_key(kind: str, host: str, title: str) -> str:
    normalized = _norm(title)
    # Without a verified global identifier, host scope prevents false merges
    # such as unrelated products or businesses sharing a generic display title.
    return kind + "|" + host + "|" + normalized


def _has_commerce_signal(product: object) -> bool:
    if not isinstance(product, dict):
        return False
    return any(
        str(product.get(key) or "").strip()
        for key in ("sku", "mpn", "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "price", "availability")
    )


def _product_entity_key(record_type: str, host: str, title: str, attributes: dict) -> str:
    gtin = _norm(attributes.get("gtin", ""))
    sku = _norm(attributes.get("sku", ""))
    brand = _norm(attributes.get("brand", ""))
    if gtin:
        return record_type + "|gtin|" + gtin
    if sku and brand:
        return record_type + "|brand|" + brand + "|sku|" + sku
    if sku:
        return record_type + "|host|" + host + "|sku|" + sku
    return _record_entity_key(record_type, host, title)


def _brand_entity_key(name: str) -> str:
    return "brand|name|" + _norm(name)


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
                if name.split() and name.split()[0].casefold() in {"its", "their", "our", "his", "her", "this", "that", "these", "those"}:
                    continue
                if not 3 <= len(name) <= 100 or not re.search(r"[A-Z0-9]", name):
                    continue
                if normalized in {"more", "other", "others", "various", "etc"}:
                    continue
                match_text = match.group(0)
                catalog_cue = cue if re.search(r"\binclude\b", match_text, re.I) or ":" in match_text else cue + "_examples"
                found[(record_type, normalized)] = (record_type, name, catalog_cue)
                if len(found) >= 30:
                    return list(found.values())
    return list(found.values())


def _catalog_title(host: str, kind: str, path: str, source_titles: list[str]) -> str:

    explicit = [x for x in source_titles if CATALOG_TITLE_RE.search(x)]
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
    content_list_groups: dict[str, dict] = {}
    page_rows = []
    media_urls = set()
    media_references = {}
    eligible = 0
    pages_with_signals = 0
    content_entity_mentions = 0
    structured_product_pages = 0
    structured_catalog_pages = 0
    structured_catalog_list_count = 0
    structured_catalog_item_count = 0

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
        product_metadata = page.get("product") if is_eligible else None
        structured_product = (
            product_metadata if isinstance(product_metadata, dict)
            and product_metadata.get("type") == "product"
            and _has_commerce_signal(product_metadata) else None
        )
        product_name = _title(structured_product.get("name")) if structured_product else ""
        if not _valid_title(product_name):
            structured_product = None
            product_name = ""
        product_kind = (
            "model" if any(segment in ROOTS_BY_TYPE.get("model", set()) for segment in segments)
            else "product"
        )
        product_attributes = {}
        if structured_product:
            for key, limit in {
                "brand": 160, "sku": 120, "gtin": 32, "price": 64, "currency": 8,
                "availability": 80, "image": 2048, "url": 2048,
            }.items():
                value = structured_product.get(key)
                if isinstance(value, (str, int, float)) and str(value).strip():
                    product_attributes[key] = str(value).strip()[:limit]
            structured_product_pages += 1

        structured_lists = []
        structured_catalog = page.get("structured_catalog") if is_eligible else None
        if isinstance(structured_catalog, dict) and structured_catalog.get("version") == 1:
            for raw_list in (structured_catalog.get("lists") or [])[:2]:
                if not isinstance(raw_list, dict):
                    continue
                list_name = _title(raw_list.get("name"))
                list_kind = str(raw_list.get("record_type") or "").casefold()
                if not list_name or list_kind not in CATALOG_TYPE:
                    continue
                structured_items = []
                seen_structured_items = set()
                for raw_item in (raw_list.get("items") or [])[:8]:
                    if not isinstance(raw_item, dict):
                        continue
                    item_kind = str(raw_item.get("record_type") or list_kind).casefold()
                    item_title = _title(raw_item.get("name"))
                    item_url = canonical_url(raw_item.get("url"))
                    if item_kind != list_kind or not _valid_title(item_title) or not item_url:
                        continue
                    identity = (item_kind, item_url)
                    if identity in seen_structured_items:
                        continue
                    seen_structured_items.add(identity)
                    attributes = {}
                    for key, limit in (("brand", 80), ("sku", 64), ("gtin", 32)):
                        value = raw_item.get(key)
                        if isinstance(value, (str, int, float)) and str(value).strip():
                            attributes[key] = str(value).strip()[:limit]
                    position = raw_item.get("position")
                    if isinstance(position, int) and not isinstance(position, bool) and position > 0:
                        attributes["position"] = position
                    structured_items.append({
                        "record_type": item_kind,
                        "title": item_title,
                        "url": item_url,
                        "attributes": attributes,
                    })
                if len(structured_items) >= 2:
                    structured_lists.append({
                        "name": list_name,
                        "record_type": list_kind,
                        "items": structured_items,
                    })
        if structured_lists:
            structured_catalog_pages += 1
            structured_catalog_list_count += len(structured_lists)
            structured_catalog_item_count += sum(len(item["items"]) for item in structured_lists)

        page_media = set()
        if is_eligible:
            page_media.update({
                normalized for raw_media in MEDIA_URL_RE.findall(text)
                if (normalized := canonical_url(raw_media.rstrip(".,);]")))
            })
            if isinstance(product_metadata, dict):
                product_image = canonical_url(product_metadata.get("image"))
                if product_image:
                    page_media.add(product_image)
        media_urls.update(page_media)
        for media_url in page_media:
            media_references[(page_id, media_url)] = {
                "media_id": stable_id("media", media_url),
                "media_url": media_url,
                "source_page_id": page_id,
                "source_url": url,
                "discovered_at": page.get("fetched_at"),
            }
        explicit = _candidate_groups(page, host, segments, body, title) if is_eligible else []
        content_entities = _extract_list_entities(body) if is_eligible else []
        content_entity_mentions += len(content_entities)
        content_counts = Counter(
            entity_kind for entity_kind, _entity_title, cue in content_entities
            if not cue.endswith("_examples")
        )
        is_catalog_definition = bool(explicit) or any(count >= 2 for count in content_counts.values()) or bool(structured_lists)
        kind = _record_type(page, segments) if is_eligible else None
        if structured_product and kind in (None, "product", "model"):
            kind = product_kind
        group_key = host + "|" + kind + "|" + _group_path(kind, segments) if kind and not is_catalog_definition else None
        record_id = None
        page_record_ids = []
        if kind and not is_catalog_definition:
            record_title = product_name if structured_product and kind == product_kind else title
            entity_key = (
                _product_entity_key(kind, host, record_title, product_attributes)
                if structured_product and kind == product_kind
                else _record_entity_key(kind, host, record_title)
            )
            record_id = stable_id("record", entity_key)
            item = records.setdefault(record_id, {
                "record_id": record_id, "entity_id": stable_id("entity", entity_key),
                "canonical_entity_key": entity_key, "record_type": kind,
                "title": record_title, "description": description or text[:500],
                "source_urls": [], "source_page_ids": [], "catalog_ids": [],
                "last_seen_at": page.get("fetched_at"), "attributes": {},
            })
            item.setdefault("attributes", {})
            if structured_product and kind == product_kind:
                item["attributes"].update(product_attributes)
                item["structured_product"] = True
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
            page_record_ids.append(record_id)

        page_group_keys = []
        explicit_keys_by_kind = defaultdict(list)
        for key, catalog_kind, path, evidence in explicit:
            explicit_groups.setdefault(key, {
                "catalog_key": key, "catalog_type": CATALOG_TYPE[catalog_kind],
                "record_type": catalog_kind, "path": path, "evidence": set(),
            })
            explicit_groups[key]["evidence"].update(evidence)
            explicit_keys_by_kind[catalog_kind].append(key)
            group_pages[key].add(page_id)
            group_urls[key].add(url)
            if title and title not in group_titles[key]:
                group_titles[key].append(title)
            page_group_keys.append(key)

        if structured_product:
            product_entity_key = _product_entity_key(product_kind, host, product_name, product_attributes)
            product_record_id = stable_id("record", product_entity_key)
            product_item = records.setdefault(product_record_id, {
                "record_id": product_record_id,
                "entity_id": stable_id("entity", product_entity_key),
                "canonical_entity_key": product_entity_key,
                "record_type": product_kind,
                "title": product_name,
                "description": description or text[:500],
                "source_urls": [], "source_page_ids": [], "catalog_ids": [],
                "last_seen_at": page.get("fetched_at"), "attributes": {},
                "structured_product": True,
            })
            product_item.setdefault("attributes", {}).update(product_attributes)
            product_item["structured_product"] = True
            if url not in product_item["source_urls"]:
                product_item["source_urls"].append(url)
            if page_id not in product_item["source_page_ids"]:
                product_item["source_page_ids"].append(page_id)
            if page.get("fetched_at") and (not product_item.get("last_seen_at") or page["fetched_at"] > product_item["last_seen_at"]):
                product_item["last_seen_at"] = page["fetched_at"]
            product_targets = explicit_keys_by_kind.get(product_kind) or [
                host + "|" + product_kind + "|" + _content_group_path(product_kind, segments)
            ]
            for product_group_key in product_targets:
                record_groups[product_group_key].add(product_record_id)
                group_pages[product_group_key].add(page_id)
                group_urls[product_group_key].add(url)
                if title and title not in group_titles[product_group_key]:
                    group_titles[product_group_key].append(title)
                page_group_keys.append(product_group_key)
            if product_record_id not in page_record_ids:
                page_record_ids.append(product_record_id)
            if record_id is None:
                record_id = product_record_id

            brand_name = _title(product_attributes.get("brand", ""))
            if brand_name and _valid_title(brand_name) and _norm(brand_name) != _norm(product_name):
                brand_entity_key = _brand_entity_key(brand_name)
                brand_record_id = stable_id("record", brand_entity_key)
                brand_item = records.setdefault(brand_record_id, {
                    "record_id": brand_record_id,
                    "entity_id": stable_id("entity", brand_entity_key),
                    "canonical_entity_key": brand_entity_key,
                    "record_type": "brand",
                    "title": brand_name,
                    "description": "Brand identified in structured product metadata.",
                    "source_urls": [], "source_page_ids": [], "catalog_ids": [],
                    "last_seen_at": page.get("fetched_at"),
                    "attributes": {"metadata_source": "structured_product_metadata"},
                    "structured_brand": True,
                })
                brand_item.setdefault("attributes", {}).setdefault("metadata_source", "structured_product_metadata")
                brand_item["structured_brand"] = True
                if url not in brand_item["source_urls"]:
                    brand_item["source_urls"].append(url)
                if page_id not in brand_item["source_page_ids"]:
                    brand_item["source_page_ids"].append(page_id)
                if page.get("fetched_at") and (not brand_item.get("last_seen_at") or page["fetched_at"] > brand_item["last_seen_at"]):
                    brand_item["last_seen_at"] = page["fetched_at"]
                brand_targets = explicit_keys_by_kind.get("brand") or [host + "|brand|/brands"]
                for brand_group_key in brand_targets:
                    record_groups[brand_group_key].add(brand_record_id)
                    group_pages[brand_group_key].add(page_id)
                    group_urls[brand_group_key].add(url)
                    if title and title not in group_titles[brand_group_key]:
                        group_titles[brand_group_key].append(title)
                    page_group_keys.append(brand_group_key)
                if brand_record_id not in page_record_ids:
                    page_record_ids.append(brand_record_id)

        for entity_kind, entity_title, cue in content_entities:
            entity_key = _record_entity_key(entity_kind, host, entity_title)
            content_record_id = stable_id("record", entity_key)
            item = records.setdefault(content_record_id, {
                "record_id": content_record_id, "entity_id": stable_id("entity", entity_key),
                "canonical_entity_key": entity_key, "record_type": entity_kind,
                "title": entity_title,
                "description": (
                    f"{'Example' if cue.endswith('_examples') else 'Explicit'} "
                    f"{cue.removesuffix('_examples')} list item extracted from {title or host}."
                ),
                "source_urls": [], "source_page_ids": [], "catalog_ids": [],
                "last_seen_at": page.get("fetched_at"), "content_extracted": True,
                "attributes": {},
            })
            item["content_extracted"] = True
            item.setdefault("attributes", {})
            if url not in item["source_urls"]:
                item["source_urls"].append(url)
            if page_id not in item["source_page_ids"]:
                item["source_page_ids"].append(page_id)
            if page.get("fetched_at") and (not item.get("last_seen_at") or page["fetched_at"] > item["last_seen_at"]):
                item["last_seen_at"] = page["fetched_at"]
            if content_record_id not in page_record_ids:
                page_record_ids.append(content_record_id)

            target_keys = explicit_keys_by_kind.get(entity_kind)
            if target_keys:
                group_keys = target_keys
            else:
                content_path = _content_group_path(entity_kind, segments)
                content_key = host + "|" + entity_kind + "|" + content_path
                group_keys = [content_key]
                if content_counts[entity_kind] >= 2:
                    content_list_groups.setdefault(content_key, {
                        "catalog_key": content_key, "catalog_type": CATALOG_TYPE[entity_kind],
                        "record_type": entity_kind, "path": content_path, "evidence": set(),
                    })
                    content_list_groups[content_key]["evidence"].add("explicit_content_list:" + cue)

            for group_key_for_record in group_keys:
                record_groups[group_key_for_record].add(content_record_id)
                group_pages[group_key_for_record].add(page_id)
                group_urls[group_key_for_record].add(url)
                if title and title not in group_titles[group_key_for_record]:
                    group_titles[group_key_for_record].append(title)
                page_group_keys.append(group_key_for_record)

        for structured_list in structured_lists:
            list_kind = structured_list["record_type"]
            list_name = structured_list["name"]
            list_slug = re.sub(r"[^a-z0-9]+", "-", _norm(list_name)).strip("-")[:80]
            if not list_slug:
                continue
            parent_path = _content_group_path(list_kind, segments)
            generic_list_names = {
                "product": {"products", "items", "catalog", "catalogue"},
                "model": {"models", "watches", "catalog", "catalogue"},
                "brand": {"brands", "catalog", "catalogue"},
                "business": {"businesses", "business", "stores", "catalog", "catalogue"},
                "place": {"places", "locations", "catalog", "catalogue"},
                "service": {"services", "catalog", "catalogue"},
                "event": {"events", "catalog", "catalogue"},
                "dataset": {"datasets", "data sets", "catalog", "catalogue"},
                "article": {"articles", "publications", "catalog", "catalogue"},
                "software": {"software", "tools", "catalog", "catalogue"},
                "person": {"people", "authors", "catalog", "catalogue"},
                "organization": {"organizations", "agencies", "catalog", "catalogue"},
                "media": {"media", "images", "videos", "catalog", "catalogue"},
                "resource": {"resources", "catalog", "catalogue"},
                "subject": {"subjects", "topics", "catalog", "catalogue"},
            }
            if _norm(list_name) in generic_list_names.get(list_kind, set()):
                path = parent_path
            else:
                path = (parent_path.rstrip("/") + "/" + list_slug) if parent_path != "/" else "/" + list_slug
            structured_key = host + "|" + list_kind + "|" + path
            group_info = content_list_groups.setdefault(structured_key, {
                "catalog_key": structured_key,
                "catalog_type": CATALOG_TYPE[list_kind],
                "record_type": list_kind,
                "path": path,
                "evidence": set(),
                "title": list_name,
            })
            group_info["evidence"].update({"structured_item_list", "schema_org_itemlist"})
            group_pages[structured_key].add(page_id)
            group_urls[structured_key].add(url)
            if list_name not in group_titles[structured_key]:
                group_titles[structured_key].append(list_name)
            page_group_keys.append(structured_key)

            for entry in structured_list["items"]:
                record_title = entry["title"]
                attributes = dict(entry.get("attributes") or {})
                if list_kind in ("product", "model"):
                    entity_key = _product_entity_key(list_kind, host, record_title, attributes)
                elif list_kind == "brand":
                    entity_key = _brand_entity_key(record_title)
                else:
                    entity_key = _record_entity_key(list_kind, host, record_title)
                structured_record_id = stable_id("record", entity_key)
                structured_item = records.setdefault(structured_record_id, {
                    "record_id": structured_record_id,
                    "entity_id": stable_id("entity", entity_key),
                    "canonical_entity_key": entity_key,
                    "record_type": list_kind,
                    "title": record_title,
                    "description": description or text[:500],
                    "source_urls": [],
                    "source_page_ids": [],
                    "catalog_ids": [],
                    "last_seen_at": page.get("fetched_at"),
                    "attributes": {},
                    "structured_catalog_item": True,
                })
                structured_item.setdefault("attributes", {}).update(attributes)
                structured_item["structured_catalog_item"] = True
                item_url = entry["url"]
                if item_url not in structured_item["source_urls"]:
                    structured_item["source_urls"].append(item_url)
                if page_id not in structured_item["source_page_ids"]:
                    structured_item["source_page_ids"].append(page_id)
                if page.get("fetched_at") and (
                    not structured_item.get("last_seen_at")
                    or page["fetched_at"] > structured_item["last_seen_at"]
                ):
                    structured_item["last_seen_at"] = page["fetched_at"]
                record_groups[structured_key].add(structured_record_id)
                group_pages[structured_key].add(page_id)
                group_urls[structured_key].add(url)
                if structured_record_id not in page_record_ids:
                    page_record_ids.append(structured_record_id)

        if group_key:
            page_group_keys.append(group_key)
        if kind or explicit or content_entities or structured_product or structured_lists:
            pages_with_signals += 1
        page_rows.append({
            "page_id": page_id, "url": url, "eligible": is_eligible,
            "catalog_definition": is_catalog_definition,
            "record_type": kind, "record_id": record_id,
            "record_ids": sorted(set(page_record_ids)),
            "catalog_group_keys": sorted(set(page_group_keys)),
            "media_reference_count": len(page_media),
            "content_entity_count": len(content_entities),
            "structured_product": bool(structured_product),
            "structured_catalog_list_count": len(structured_lists),
            "structured_catalog_item_count": sum(len(item["items"]) for item in structured_lists),
            "fetched_at": page.get("fetched_at"),
        })

    candidate_keys = set(explicit_groups) | set(content_list_groups)
    for key, ids in record_groups.items():
        # Ordinary article mentions do not become catalogs merely because a
        # single page names multiple things. Repeated records need two pages;
        # an explicit content list is handled separately above.
        if len(ids) >= 2 and len(group_pages.get(key, set())) >= 2:
            candidate_keys.add(key)

    page_by_id = {item["page_id"]: item for item in page_rows if item.get("page_id")}
    catalogs = {}
    for key in sorted(candidate_keys):
        is_declared = key in explicit_groups
        is_content_list = key in content_list_groups
        if is_declared:
            info = explicit_groups[key]
            kind, path = info["record_type"], info["path"]
            evidence = sorted(info["evidence"])
        elif is_content_list:
            info = content_list_groups[key]
            kind, path = info["record_type"], info["path"]
            evidence = sorted(info["evidence"])
        else:
            _, kind, path = key.split("|", 2)
            evidence = ["multiple_distinct_records_across_source_pages"]
        catalog_id = stable_id("catalog", key)
        source_page_ids = sorted(group_pages.get(key, set()))
        source_rows = [page_by_id.get(page_id, {}) for page_id in source_page_ids]
        source_timestamps = sorted(stamp for stamp in (_timestamp_iso(row.get("fetched_at")) for row in source_rows) if stamp)
        record_ids = sorted(record_groups.get(key, set()))
        structured_info = content_list_groups.get(key, {}) if is_content_list else {}
        generic_titles = {"products", "items", "models", "watches", "brands", "businesses", "stores", "places", "locations", "services", "events", "datasets", "data sets", "articles", "publications", "software", "tools", "people", "authors", "organizations", "agencies", "media", "images", "videos", "resources", "subjects", "topics", "catalog", "catalogue"}
        declared_list_title = structured_info.get("title")
        title = (
            declared_list_title
            if isinstance(declared_list_title, str) and _norm(declared_list_title) not in generic_titles
            else _catalog_title(key.split("|", 1)[0], kind, path, group_titles.get(key, []))
        )
        if not is_declared and not is_content_list:
            title += " — referenced in indexed pages"
        evidence_strength = (
            "high" if is_declared and ("listing_language" in evidence or "catalog_title" in evidence)
            else "medium" if is_declared or is_content_list else "derived"
        )
        catalog_status = (
            "definition_only" if (is_declared or is_content_list) and not record_ids
            else "candidate_needs_source_validation" if (is_declared or is_content_list)
            else "derived_candidate_needs_source_validation"
        )
        catalog_origin = (
            "source_declared_collection" if is_declared
            else "structured_schema_collection" if is_content_list and "structured_item_list" in evidence
            else "content_list_declared_collection" if is_content_list
            else "derived_from_repeated_records"
        )
        catalogs[key] = {
            "catalog_id": catalog_id, "source_key": key, "title": title,
            "catalog_type": CATALOG_TYPE[kind], "record_type": kind,
            "subject": path.strip("/").split("/")[-1].replace("-", " ")[:160],
            "source_host": key.split("|", 1)[0], "source_url": min(group_urls[key]) if group_urls.get(key) else "",
            "source_page_ids": source_page_ids, "source_page_count": len(source_page_ids),
            "first_seen_at": source_timestamps[0] if source_timestamps else None,
            "last_seen_at": source_timestamps[-1] if source_timestamps else None,
            "record_ids": record_ids, "record_count": len(record_ids),
            "parent_catalog_id": None, "evidence": evidence,
            "catalog_origin": catalog_origin,
            "is_source_declared_catalog": is_declared,
            "catalog_definition": is_declared or is_content_list,
            "evidence_strength": evidence_strength, "catalog_status": catalog_status,
            "source_terms_status": "not_reviewed",
            "rights_status": "publicly_accessible_not_cleared_for_redistribution",
            "commercial_eligible": False, "status": catalog_status, "verified": False,
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
        item["source_terms_status"] = "not_reviewed"
        item["rights_status"] = "publicly_accessible_not_cleared_for_redistribution"
        item["commercial_eligible"] = False

    record_list = sorted(records.values(), key=lambda x: (x["record_type"], x["title"].casefold(), x["record_id"]))
    catalog_list = sorted(catalogs.values(), key=lambda x: (x["catalog_type"], x["title"].casefold(), x["catalog_id"]))
    type_counts = dict(sorted(Counter(x["record_type"] for x in record_list).items()))
    source_total = max(source_total if isinstance(source_total, int) and source_total >= 0 else source_page_rows, source_page_rows)
    unique_source_total = max(len(pages), source_total - duplicate_source_page_rows)
    source_set_complete = len(pages) == unique_source_total
    source_row_coverage = (len(pages) / source_total * 100.0) if source_total else 0.0
    unique_coverage = (len(pages) / unique_source_total * 100.0) if unique_source_total else 0.0
    eligible_pages_analyzed = sum(1 for item in page_rows if item.get("eligible") is True)
    if eligible_pages_analyzed != eligible:
        raise ValueError("eligible page metrics do not match the analyzed page rows")
    eligible_coverage = 100.0 if source_set_complete else None
    eligibility_rate = (eligible / len(pages) * 100.0) if pages else 0.0
    source_row_deduplication = (duplicate_source_page_rows / source_page_rows * 100.0) if source_page_rows else 0.0
    page_refs = sum(len(x.get("source_page_ids", [])) for x in catalog_list)
    metrics = {
        "pages_analyzed": len(pages),
        "source_page_rows": source_page_rows,
        "unique_source_pages": len(pages),
        "duplicate_source_page_rows": duplicate_source_page_rows,
        "eligible_pages": eligible,
        "eligible_pages_analyzed": eligible_pages_analyzed,
        "source_index_unique_pages": unique_source_total,
        "eligibility_rate_percentage": round(eligibility_rate, 4),
        "source_row_coverage_percentage": round(source_row_coverage, 4),
        "source_row_deduplication_percentage": round(source_row_deduplication, 4),
        "eligible_page_coverage_status": "complete" if source_set_complete else "unknown_incomplete_source_set",
        "unique_catalogs_discovered": len(catalog_list),
        "unique_catalogs_by_type": dict(sorted(Counter(x["catalog_type"] for x in catalog_list).items())),
        "source_declared_catalogs": sum(bool(x.get("is_source_declared_catalog")) for x in catalog_list),
        "derived_catalogs": sum(x.get("catalog_origin") == "derived_from_repeated_records" for x in catalog_list),
        "content_list_catalogs": sum(x.get("catalog_origin") == "content_list_declared_collection" for x in catalog_list),
        "structured_schema_catalogs": sum(x.get("catalog_origin") == "structured_schema_collection" for x in catalog_list),
        "catalogs_with_records": sum(bool(x.get("record_ids")) for x in catalog_list),
        "catalogs_without_records": sum(not x.get("record_ids") for x in catalog_list),
        "catalogs_with_multiple_records": sum(len(x.get("record_ids", [])) >= 2 for x in catalog_list),
        "records_assigned_to_catalogs": sum(bool(x.get("catalog_ids")) for x in record_list),
        "unassigned_records": sum(not x.get("catalog_ids") for x in record_list),
        "records_with_provenance": sum(bool(x.get("source_urls") and x.get("source_page_ids")) for x in record_list),
        "records_with_timestamps": sum(x.get("last_seen_at") is not None for x in record_list),
        "source_terms_reviewed_records": sum(x.get("source_terms_status") != "not_reviewed" for x in record_list),
        "commercially_eligible_records": sum(bool(x.get("commercial_eligible")) for x in record_list),
        "catalog_page_associations": page_refs,
        "catalog_page_associations": page_refs,
        "catalog_candidate_page_references": page_refs,
        "catalog_candidate_occurrences": page_refs,
        "duplicate_catalog_candidates": max(0, page_refs - len(catalog_list)),
        "duplicate_catalog_candidate_page_references": max(0, page_refs - len(catalog_list)),
        "subcatalogs_discovered": sum(bool(x["parent_catalog_id"]) for x in catalog_list),
        "catalog_definition_pages": sum(bool(x.get("catalog_definition")) for x in page_rows),
        "unique_records": len(record_list),
        "cross_host_record_merges": 0,
        "unique_entities": len({x.get("entity_id") for x in record_list if x.get("entity_id")}),
        "unique_records_by_type": type_counts,
        "content_entity_mentions": content_entity_mentions,
        "content_extracted_unique_records": sum(1 for item in record_list if item.get("content_extracted")),
        "structured_product_pages": structured_product_pages,
        "structured_product_records": sum(1 for item in record_list if item.get("structured_product")),
        "structured_brand_records": sum(1 for item in record_list if item.get("structured_brand")),
        "structured_catalog_pages": structured_catalog_pages,
        "structured_catalog_lists": structured_catalog_list_count,
        "structured_catalog_items": structured_catalog_item_count,
        "structured_catalog_records": sum(1 for item in record_list if item.get("structured_catalog_item")),
        "media_references": len(media_references),
        "unique_media_urls": len(media_urls),
        "records_staged": len(record_list),
        "records_published": 0,
        "catalogs_verified": 0,
        "pages_with_catalog_signals": pages_with_signals,
        "coverage_percentage": round(unique_coverage, 4),
        "unique_source_coverage_percentage": round(unique_coverage, 4),
        "eligible_page_coverage_percentage": round(eligible_coverage, 4) if eligible_coverage is not None else None,
        "eligible_unique_source_page_coverage_percentage": round(eligible_coverage, 4) if eligible_coverage is not None else None,
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


def _merge_cross_host_records(records: list[dict]) -> tuple[list[dict], int]:
    """Merge globally identified records shared by otherwise independent host partitions."""
    merged: dict[str, dict] = {}
    merge_count = 0

    def stamp(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except (ValueError, OverflowError):
                return 0.0
        return 0.0

    for source in records:
        item = dict(source)
        item["source_urls"] = list(source.get("source_urls", []))
        item["source_page_ids"] = list(source.get("source_page_ids", []))
        item["catalog_ids"] = list(source.get("catalog_ids", []))
        item["attributes"] = dict(source.get("attributes", {})) if isinstance(source.get("attributes"), dict) else {}
        record_id = item.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("host analysis produced a record without a stable record ID")
        current = merged.get(record_id)
        if current is None:
            merged[record_id] = item
            continue
        if current.get("canonical_entity_key") != item.get("canonical_entity_key"):
            raise ValueError("the same record ID maps to different canonical entity keys")

        merge_count += 1
        incoming_is_newer = stamp(item.get("last_seen_at")) > stamp(current.get("last_seen_at"))
        if incoming_is_newer:
            current["last_seen_at"] = item.get("last_seen_at")
            if item.get("description"):
                current["description"] = item["description"]
            current.setdefault("attributes", {}).update(item["attributes"])
        else:
            for key, value in item["attributes"].items():
                current.setdefault("attributes", {}).setdefault(key, value)

        for key in ("source_urls", "source_page_ids", "catalog_ids"):
            current[key] = sorted(set(current.get(key, [])) | set(item.get(key, [])))
        for key in ("structured_product", "structured_brand", "content_extracted", "structured_catalog_item"):
            if item.get(key):
                current[key] = True
        current["commercial_eligible"] = False
        current["source_terms_status"] = "not_reviewed"
        current["source_url"] = current["source_urls"][0] if current["source_urls"] else ""
        current["source_page_count"] = len(current["source_page_ids"])

    for item in merged.values():
        item["source_urls"] = sorted(set(item.get("source_urls", [])))
        item["source_page_ids"] = sorted(set(item.get("source_page_ids", [])))
        item["catalog_ids"] = sorted(set(item.get("catalog_ids", [])))
        item["source_url"] = item["source_urls"][0] if item["source_urls"] else ""
        item["source_page_count"] = len(item["source_page_ids"])
    return list(merged.values()), merge_count


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
    structured_catalog_lists = 0
    structured_catalog_items = 0
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
        structured_catalog_lists += shard["metrics"].get("structured_catalog_lists", 0)
        structured_catalog_items += shard["metrics"].get("structured_catalog_items", 0)
        eligible += shard["metrics"]["eligible_pages"]
        signals += shard["metrics"]["pages_with_catalog_signals"]
    all_records, cross_host_record_merges = _merge_cross_host_records(all_records)
    all_catalogs.sort(key=lambda x: (x["catalog_type"], x["title"].casefold(), x["catalog_id"]))
    all_records.sort(key=lambda x: (x["record_type"], x["title"].casefold(), x["record_id"]))
    all_page_rows.sort(key=lambda x: (x.get("url") or "", x.get("page_id") or ""))
    source_total = max(source_total if isinstance(source_total, int) and source_total >= 0 else source_page_rows, source_page_rows)
    unique_source_total = max(len(pages), source_total - duplicate_source_page_rows)
    source_set_complete = len(pages) == unique_source_total
    source_row_coverage = (len(pages) / source_total * 100.0) if source_total else 0.0
    unique_coverage = (len(pages) / unique_source_total * 100.0) if unique_source_total else 0.0
    eligible_pages_analyzed = sum(1 for item in all_page_rows if item.get("eligible") is True)
    if eligible_pages_analyzed != eligible:
        raise ValueError("eligible page metrics do not match the analyzed page rows")
    eligible_coverage = 100.0 if source_set_complete else None
    eligibility_rate = (eligible / len(pages) * 100.0) if pages else 0.0
    source_row_deduplication = (duplicate_source_page_rows / source_page_rows * 100.0) if source_page_rows else 0.0
    page_refs = sum(len(x.get("source_page_ids", [])) for x in all_catalogs)
    type_counts = dict(sorted(Counter(x["record_type"] for x in all_records).items()))
    metrics = {
        "pages_analyzed": len(pages),
        "source_page_rows": source_page_rows,
        "unique_source_pages": len(pages),
        "duplicate_source_page_rows": duplicate_source_page_rows,
        "eligible_pages": eligible,
        "eligible_pages_analyzed": eligible_pages_analyzed,
        "source_index_unique_pages": unique_source_total,
        "eligibility_rate_percentage": round(eligibility_rate, 4),
        "source_row_coverage_percentage": round(source_row_coverage, 4),
        "source_row_deduplication_percentage": round(source_row_deduplication, 4),
        "eligible_page_coverage_status": "complete" if source_set_complete else "unknown_incomplete_source_set",
        "unique_catalogs_discovered": len(all_catalogs),
        "unique_catalogs_by_type": dict(sorted(Counter(x["catalog_type"] for x in all_catalogs).items())),
        "source_declared_catalogs": sum(bool(x.get("is_source_declared_catalog")) for x in all_catalogs),
        "derived_catalogs": sum(x.get("catalog_origin") == "derived_from_repeated_records" for x in all_catalogs),
        "content_list_catalogs": sum(x.get("catalog_origin") == "content_list_declared_collection" for x in all_catalogs),
        "structured_schema_catalogs": sum(x.get("catalog_origin") == "structured_schema_collection" for x in all_catalogs),
        "catalogs_with_records": sum(bool(x.get("record_ids")) for x in all_catalogs),
        "catalogs_without_records": sum(not x.get("record_ids") for x in all_catalogs),
        "catalogs_with_multiple_records": sum(len(x.get("record_ids", [])) >= 2 for x in all_catalogs),
        "records_assigned_to_catalogs": sum(bool(x.get("catalog_ids")) for x in all_records),
        "unassigned_records": sum(not x.get("catalog_ids") for x in all_records),
        "records_with_provenance": sum(bool(x.get("source_urls") and x.get("source_page_ids")) for x in all_records),
        "records_with_timestamps": sum(x.get("last_seen_at") is not None for x in all_records),
        "source_terms_reviewed_records": sum(x.get("source_terms_status") != "not_reviewed" for x in all_records),
        "commercially_eligible_records": sum(bool(x.get("commercial_eligible")) for x in all_records),
        "catalog_page_associations": page_refs,
        "catalog_page_associations": page_refs,
        "catalog_candidate_page_references": page_refs,
        "catalog_candidate_occurrences": page_refs,
        "duplicate_catalog_candidates": max(0, page_refs - len(all_catalogs)),
        "duplicate_catalog_candidate_page_references": max(0, page_refs - len(all_catalogs)),
        "subcatalogs_discovered": sum(bool(x["parent_catalog_id"]) for x in all_catalogs),
        "catalog_definition_pages": sum(bool(x.get("catalog_definition")) for x in all_page_rows),
        "unique_records": len(all_records),
        "cross_host_record_merges": cross_host_record_merges,
        "unique_entities": len({x.get("entity_id") for x in all_records if x.get("entity_id")}),
        "unique_records_by_type": type_counts,
        "content_entity_mentions": content_entity_mentions,
        "content_extracted_unique_records": sum(1 for item in all_records if item.get("content_extracted")),
        "structured_product_pages": sum(1 for item in all_page_rows if item.get("structured_product")),
        "structured_product_records": sum(1 for item in all_records if item.get("structured_product")),
        "structured_brand_records": sum(1 for item in all_records if item.get("structured_brand")),
        "structured_catalog_pages": sum(1 for item in all_page_rows if item.get("structured_catalog_list_count", 0) > 0),
        "structured_catalog_lists": structured_catalog_lists,
        "structured_catalog_items": structured_catalog_items,
        "structured_catalog_records": sum(1 for item in all_records if item.get("structured_catalog_item")),
        "media_references": len({(x["source_page_id"], x["media_url"]) for x in all_media_references}),
        "unique_media_urls": len(all_media), "records_staged": len(all_records),
        "records_published": 0, "catalogs_verified": 0,
        "pages_with_catalog_signals": signals,
        "coverage_percentage": round(unique_coverage, 4),
        "unique_source_coverage_percentage": round(unique_coverage, 4),
        "eligible_page_coverage_percentage": round(eligible_coverage, 4) if eligible_coverage is not None else None,
        "eligible_unique_source_page_coverage_percentage": round(eligible_coverage, 4) if eligible_coverage is not None else None,
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


def _searchable_attribute_text(attributes: object) -> str:
    if not isinstance(attributes, dict):
        return ""
    searchable = {"brand", "model", "sku", "mpn", "gtin", "manufacturer", "category"}
    return " ".join(
        str(value) for key, value in attributes.items()
        if str(key).casefold() in searchable and isinstance(value, (str, int, float))
    )


def search_catalog_network(query: str, analysis: dict, limit: int = 10) -> list[dict]:
    tokens = list(dict.fromkeys(_norm(query).split()))
    if not tokens or limit <= 0:
        return []
    token_set = set(tokens)
    query_phrase = " ".join(tokens)
    records = {x["record_id"]: x for x in analysis.get("records", [])}
    product_types = {"product", "brand", "model"}
    location_types = {"place", "business", "service", "organization", "dataset"}
    content_types = {"article", "resource", "media", "subject"}
    product_terms = {
        "product", "products", "model", "models", "watch", "watches",
        "buy", "shop", "retailer", "retailers", "dealer", "dealers",
        "price", "prices", "reference", "references",
    }
    business_terms = {
        "business", "businesses", "restaurant", "restaurants", "hotel",
        "hotels", "store", "stores", "shop", "shops", "venue", "venues",
        "dealer", "dealers",
    }
    event_terms = {
        "event", "events", "concert", "concerts", "festival", "festivals",
        "calendar", "schedule", "schedules", "grand", "prix",
    }
    content_intent = bool(token_set & CONTENT_INTENT_TOKENS)
    event_intent = bool(token_set & event_terms)
    business_intent = bool(token_set & business_terms)
    product_term_intent = bool(token_set & product_terms)

    def query_matches_record(record: dict) -> bool:
        title_tokens = set(_norm(record.get("title", "")).split())
        description_tokens = set(_norm(record.get("description", "")).split())
        attribute_tokens = set(_norm(_searchable_attribute_text(record.get("attributes", {}))).split())
        return token_set.issubset(title_tokens | description_tokens | attribute_tokens)

    global_product_match = any(
        str(record.get("record_type", "")).casefold() in product_types
        and query_matches_record(record)
        for record in records.values()
    )
    global_place_match = any(
        str(record.get("record_type", "")).casefold() == "place"
        and query_matches_record(record)
        for record in records.values()
    )
    product_intent = not content_intent and not business_intent and (product_term_intent or global_product_match)
    geo_intent = (
        len(tokens) >= 2 and global_place_match
        and not content_intent and not event_intent and not business_intent
    )
    location_business_intent = business_intent and not content_intent
    results = []
    for catalog in analysis.get("catalogs", []):
        related = [records[x] for x in catalog.get("record_ids", []) if x in records]
        catalog_text = _norm(" ".join((catalog.get("title", ""), catalog.get("subject", ""))))
        catalog_tokens = set(catalog_text.split())
        catalog_match = token_set.issubset(catalog_tokens)
        record_type = str(catalog.get("record_type", "")).casefold()
        content_record = record_type in content_types
        matched = []
        for record in related:
            title_norm = _norm(record.get("title", ""))
            title_tokens = set(title_norm.split())
            description_tokens = set(_norm(record.get("description", "")).split())
            attribute_tokens = set(_norm(_searchable_attribute_text(record.get("attributes", {}))).split())
            title_match = token_set.issubset(title_tokens)
            if content_record and not content_intent and not title_match:
                continue
            combined_tokens = catalog_tokens | title_tokens | attribute_tokens
            if not content_record or content_intent:
                combined_tokens |= description_tokens
            if not token_set.issubset(combined_tokens):
                continue
            score = 20
            if title_match:
                score += 40
            if query_phrase and query_phrase in title_norm:
                score += 10
            score += 2 * len(token_set & description_tokens)
            score += 4 * len(token_set & attribute_tokens)
            matched.append((score, record))
        if not matched:
            continue
        ranked = sorted(matched, key=lambda x: (-x[0], x[1]["title"].casefold()))[:5]
        matched_types = {str(record.get("record_type", "")).casefold() for _, record in ranked}
        catalog_type = str(catalog.get("catalog_type", "")).casefold()
        record_scores = [score for score, _record in ranked]
        aggregate_record_score = max(record_scores) + min(30, sum(record_scores) - max(record_scores))
        catalog_score = 100 + (10 * len(tokens)) if catalog_match else 0

        type_bonus = 0
        if product_intent:
            if matched_types & product_types:
                type_bonus = 180
            elif matched_types & content_types:
                type_bonus = -100
        elif geo_intent:
            if catalog_type == "geographic" and "place" in matched_types:
                type_bonus = 180
            elif catalog_type == "business_directory" and "business" in matched_types:
                type_bonus = 145
            elif catalog_type == "service_directory" and "service" in matched_types:
                type_bonus = 140
            elif catalog_type == "organization_directory" and "organization" in matched_types:
                type_bonus = 110
            elif catalog_type == "events" and "event" in matched_types:
                type_bonus = 60
            elif matched_types & content_types:
                type_bonus = -100
        elif location_business_intent:
            if catalog_type == "business_directory" and "business" in matched_types:
                type_bonus = 180
            elif catalog_type == "service_directory" and "service" in matched_types:
                type_bonus = 160
            elif catalog_type == "geographic" and "place" in matched_types:
                type_bonus = 100
            elif matched_types & content_types:
                type_bonus = -80
        elif event_intent:
            if catalog_type == "events" and "event" in matched_types:
                type_bonus = 180
            elif matched_types & content_types:
                type_bonus = -80
        else:
            if catalog_type == "geographic":
                type_bonus = 70 if len(tokens) >= 2 and "place" in matched_types else -20
            elif catalog_type == "commerce":
                type_bonus = 50 if matched_types & product_types else -20
            elif catalog_type == "business_directory":
                type_bonus = 40 if "business" in matched_types else -15
            elif catalog_type == "service_directory":
                type_bonus = 40 if "service" in matched_types else -15

        if product_intent and matched_types & product_types:
            relevance_class = "typed_product_match"
        elif geo_intent and matched_types & location_types:
            relevance_class = "typed_location_match"
        elif geo_intent and "event" in matched_types:
            relevance_class = "related_event_match"
        elif location_business_intent and matched_types & {"business", "service"}:
            relevance_class = "typed_business_match"
        elif event_intent and "event" in matched_types:
            relevance_class = "typed_event_match"
        elif matched_types & content_types:
            relevance_class = "related_mention"
        elif catalog_match:
            relevance_class = "catalog_match"
        else:
            relevance_class = "record_match"
        query_intent = (
            "product" if product_intent else
            "location" if geo_intent else
            "business" if location_business_intent else
            "event" if event_intent else
            "content" if content_intent else
            "general"
        )
        relevance_label = {
            "typed_product_match": "Product or model match",
            "typed_location_match": "Place or local directory match",
            "typed_business_match": "Business or service match",
            "typed_event_match": "Event catalog match",
            "related_event_match": "Related event mention for this location",
            "related_mention": "Related mention, not a dedicated catalog match",
            "catalog_match": "Catalog title match",
            "record_match": "Record match",
        }[relevance_class]
        results.append({
            "catalog_id": catalog["catalog_id"], "title": catalog["title"],
            "catalog_type": catalog["catalog_type"], "record_type": catalog.get("record_type"),
            "subject": catalog.get("subject"), "source_host": catalog.get("source_host"),
            "source_url": catalog.get("source_url"),
            "catalog_origin": catalog.get("catalog_origin", "unknown"),
            "is_source_declared_catalog": bool(catalog.get("is_source_declared_catalog")),
            "evidence_strength": catalog.get("evidence_strength", "unknown"),
            "catalog_status": catalog.get("catalog_status", catalog.get("status")),
            "source_terms_status": catalog.get("source_terms_status", "not_reviewed"),
            "rights_status": catalog.get("rights_status", "publicly_accessible_not_cleared_for_redistribution"),
            "commercial_eligible": bool(catalog.get("commercial_eligible", False)),
            "verified": bool(catalog.get("verified", False)),
            "status": catalog.get("status", "candidate_needs_source_validation"),
            "match_scope": "catalog" if catalog_match else "records",
            "query_intent": query_intent,
            "relevance_class": relevance_class,
            "relevance_label": relevance_label,
            "score": catalog_score + type_bonus + aggregate_record_score,
            "record_count": len(related),
            "source_page_count": catalog.get("source_page_count", len(catalog.get("source_page_ids", []))),
            "matched_records": [{
                "record_id": record["record_id"], "entity_id": record.get("entity_id"),
                "title": record["title"], "record_type": record["record_type"],
                "description": record.get("description", ""),
                "attributes": record.get("attributes", {}),
                "source_url": record.get("source_url", ""),
                "source_urls": record.get("source_urls", []),
                "source_page_count": record.get("source_page_count", 0),
                "last_seen_at": record.get("last_seen_at"),
            } for _, record in ranked],
        })
    return sorted(results, key=lambda x: (-x["score"], -len(x["matched_records"]), x["title"].casefold()))[:limit]


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




def _normalized_policy_host(value: object) -> str:
    if not isinstance(value, str):
        return ""
    host = value.strip().lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _policy_hosts_from_urls(urls: object) -> set[str]:
    hosts = set()
    if not isinstance(urls, list):
        return hosts
    for value in urls:
        if not isinstance(value, str):
            continue
        try:
            host = _normalized_policy_host(urlsplit(value).hostname or "")
        except ValueError:
            host = ""
        if host:
            hosts.add(host)
    return hosts


def _policy_host_matches(source_host: str, aliases: set[str]) -> bool:
    return any(
        source_host == alias or source_host.endswith("." + alias)
        for alias in aliases if alias
    )


def apply_source_policy_reviews(
    analysis: dict,
    policy_path: Path = DEFAULT_SOURCE_POLICY_REVIEWS,
) -> dict:
    """Annotate matching source-level policy reviews without clearing record-level rights."""
    raw = Path(policy_path).read_bytes()
    register = json.loads(raw.decode("utf-8"))
    policy = register.get("policy") if isinstance(register, dict) else None
    reviews = register.get("reviews") if isinstance(register, dict) else None
    if (
        register.get("schema_version") != 1
        or not isinstance(register.get("reviewed_at"), str)
        or not register.get("reviewed_at")
        or not isinstance(policy, dict)
        or policy.get("default_status") != "candidate_only"
        or policy.get("record_level_clearance") != "not_reviewed"
        or policy.get("commercial_eligibility") is not False
        or policy.get("republication") != "not_cleared"
        or not isinstance(reviews, list)
    ):
        raise ValueError("source policy review register must remain conditional and deny-by-default")

    normalized_reviews = []
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("source policy review entry is malformed")
        host = _normalized_policy_host(review.get("host"))
        api_host = _normalized_policy_host(review.get("api_host"))
        evidence_urls = review.get("evidence_urls")
        if (
            not host
            or not isinstance(review.get("status"), str)
            or not review.get("status")
            or review.get("record_level_clearance") != "not_reviewed"
            or review.get("commercial_eligibility") is not False
            or not isinstance(evidence_urls, list)
            or not evidence_urls
        ):
            raise ValueError("source policy review entry cannot grant record-level clearance")
        safe_evidence = []
        for evidence_url in evidence_urls:
            if not isinstance(evidence_url, str):
                raise ValueError("source policy evidence URL is malformed")
            parsed = urlsplit(evidence_url)
            if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("source policy evidence must use credential-free HTTPS URLs")
            safe_evidence.append(evidence_url)
        normalized_reviews.append({
            "host": host,
            "aliases": {host, api_host},
            "status": review["status"],
            "evidence_urls": sorted(set(safe_evidence)),
        })

    matched_review_hosts = set()

    def annotate(item: dict, source_hosts: set[str]) -> str:
        matched = []
        matched_source_hosts = set()
        for review in normalized_reviews:
            covered = {
                host for host in source_hosts
                if _policy_host_matches(host, review["aliases"])
            }
            if covered:
                matched.append(review)
                matched_source_hosts.update(covered)
                matched_review_hosts.add(review["host"])
        unmatched_hosts = sorted(source_hosts - matched_source_hosts)
        matched_hosts = sorted({review["host"] for review in matched})
        if not matched:
            status = "not_reviewed"
        elif unmatched_hosts:
            status = "partial_source_review"
        else:
            status = "conditional_source_review"
        item.update({
            "source_policy_review_status": status,
            "source_policy_review_hosts": matched_hosts,
            "source_policy_review_categories": sorted({review["status"] for review in matched}),
            "source_policy_review_evidence_urls": sorted({
                url for review in matched for url in review["evidence_urls"]
            }),
            "source_policy_review_unmatched_hosts": unmatched_hosts,
            "source_policy_reviewed_at": register["reviewed_at"] if matched else None,
            "source_policy_review_scope": "source-level-only" if matched else "none",
            "record_level_clearance": "not_reviewed",
            "source_terms_status": "not_reviewed",
            "rights_status": "publicly_accessible_not_cleared_for_redistribution",
            "commercial_eligible": False,
        })
        return status

    records = analysis.get("records", [])
    catalogs = analysis.get("catalogs", [])
    if not isinstance(records, list) or not isinstance(catalogs, list):
        raise ValueError("source policy annotation requires record and catalog lists")

    for record in records:
        urls = record.get("source_urls", [])
        if not urls and record.get("source_url"):
            urls = [record["source_url"]]
        source_hosts = _policy_hosts_from_urls(urls)
        if record.get("source_host"):
            host = _normalized_policy_host(record["source_host"])
            if host:
                source_hosts.add(host)
        annotate(record, source_hosts)

    for catalog in catalogs:
        urls = [catalog.get("source_url")] if catalog.get("source_url") else []
        source_hosts = _policy_hosts_from_urls(urls)
        if catalog.get("source_host"):
            host = _normalized_policy_host(catalog["source_host"])
            if host:
                source_hosts.add(host)
        annotate(catalog, source_hosts)

    metrics = analysis.setdefault("metrics", {})
    metrics.update({
        "records_with_conditional_source_policy_review": sum(
            item.get("source_policy_review_status") == "conditional_source_review" for item in records
        ),
        "records_with_partial_source_policy_review": sum(
            item.get("source_policy_review_status") == "partial_source_review" for item in records
        ),
        "catalogs_with_conditional_source_policy_review": sum(
            item.get("source_policy_review_status") == "conditional_source_review" for item in catalogs
        ),
        "catalogs_with_partial_source_policy_review": sum(
            item.get("source_policy_review_status") == "partial_source_review" for item in catalogs
        ),
        "source_policy_review_hosts_matched": sorted(matched_review_hosts),
        "source_policy_review_register_entries": len(normalized_reviews),
        "source_terms_reviewed_records": sum(
            item.get("source_terms_status") != "not_reviewed" for item in records
        ),
        "commercially_eligible_records": sum(bool(item.get("commercial_eligible")) for item in records),
    })
    analysis["source_policy_review"] = {
        "schema_version": 1,
        "register_path": "data/source-policy-reviews.json",
        "register_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_at": register["reviewed_at"],
        "review_count": len(normalized_reviews),
        "matched_hosts": sorted(matched_review_hosts),
        "scope": "source-level-only",
        "record_level_clearance": "not_reviewed",
        "commercial_eligibility": False,
        "republication": "not_cleared",
    }
    return analysis


def _write_gzip(path: Path, payload: dict) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(compressed)
    temp.replace(path)
    return len(compressed), hashlib.sha256(compressed).hexdigest()


def _singular_search_token(token: str) -> str | None:
    if token in {"news", "series", "species", "politics", "physics", "mathematics", "economics", "ethics"}:
        return None
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "sses", "xes", "zes", "oes")) and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 3 and not token.endswith(("ss", "us", "is", "as", "os", "ws", "ics")):
        return token[:-1]
    return None


def _search_tokens(value: object) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    raw_tokens = {
        token for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE)
        if len(token) >= 2 and token not in STOP_TOKENS
    }
    tokens = set(raw_tokens)
    for token in raw_tokens:
        singular = _singular_search_token(token)
        if singular and len(singular) >= 2 and singular not in STOP_TOKENS:
            tokens.add(singular)
    return tokens


def build_search_index(analysis: dict) -> dict[str, dict]:
    """Build deterministic token-to-catalog postings for bounded Edge search."""
    catalog_ids = {
        item.get("catalog_id")
        for item in analysis.get("catalogs", [])
        if isinstance(item, dict) and isinstance(item.get("catalog_id"), str)
    }
    token_catalogs: dict[str, set[str]] = defaultdict(set)

    for catalog in analysis.get("catalogs", []):
        if not isinstance(catalog, dict):
            continue
        catalog_id = catalog.get("catalog_id")
        if catalog_id not in catalog_ids:
            continue
        for value in (catalog.get("title"), catalog.get("subject"), catalog.get("source_host")):
            for token in _search_tokens(value):
                token_catalogs[token].add(catalog_id)

    searchable_attributes = {"brand", "model", "sku", "mpn", "gtin", "manufacturer", "category"}
    for record in analysis.get("records", []):
        if not isinstance(record, dict):
            continue
        related_catalogs = [
            catalog_id for catalog_id in record.get("catalog_ids", [])
            if catalog_id in catalog_ids
        ]
        if not related_catalogs:
            continue
        tokens = set()
        tokens.update(_search_tokens(record.get("title")))
        tokens.update(_search_tokens(str(record.get("description") or "")[:2000]))
        attributes = record.get("attributes")
        if isinstance(attributes, dict):
            for key, value in attributes.items():
                if str(key).lower() in searchable_attributes and isinstance(value, (str, int, float)):
                    tokens.update(_search_tokens(value))
        for token in tokens:
            token_catalogs[token].update(related_catalogs)

    shards: dict[str, dict] = {}
    for token, related_catalogs in sorted(token_catalogs.items()):
        prefix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:2]
        shard = shards.setdefault(prefix, {"version": 1, "prefix": prefix, "tokens": {}})
        shard["tokens"][token] = sorted(related_catalogs)
    return {
        prefix: {
            **payload,
            "tokens": dict(sorted(payload["tokens"].items())),
        }
        for prefix, payload in sorted(shards.items())
    }


def write_outputs(analysis: dict, output_dir: Path) -> dict:
    apply_source_policy_reviews(analysis)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for catalog in analysis["catalogs"]:
        index_digest = hashlib.sha256(catalog["catalog_id"].encode("utf-8")).hexdigest()
        filename = index_digest[:24] + ".json.gz"
        catalog["index_file"] = "indexes/" + index_digest[:2] + "/" + filename
        catalog["record_count"] = len(catalog.get("record_ids", []))
        catalog["source_page_count"] = len(catalog.get("source_page_ids", []))
    size, digest = _write_gzip(output_dir / "pages.json.gz", {
        "version": 1, "source_index_pages": analysis["source_index_pages"],
        "pages": analysis["pages"],
    })
    files.append({"file": "pages.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["pages"])})
    search_shards = build_search_index(analysis)
    if not search_shards:
        search_shards = {"00": {"version": 1, "prefix": "00", "tokens": {}}}
    analysis["metrics"]["search_index_tokens"] = sum(len(item["tokens"]) for item in search_shards.values())
    analysis["metrics"]["search_index_shards"] = len(search_shards)
    analysis["metrics"]["search_index_catalog_postings"] = sum(
        len(catalog_ids)
        for item in search_shards.values()
        for catalog_ids in item["tokens"].values()
    )
    size, digest = _write_gzip(output_dir / "registry.json.gz", {
        "version": 1, "analysis_version": analysis.get("analysis_version", ANALYZER_VERSION),
        "status": "candidate_staging_not_production",
        "metrics": analysis["metrics"], "source_policy_review": analysis.get("source_policy_review"), "catalogs": analysis["catalogs"],
    })
    files.append({"file": "registry.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["catalogs"])})
    records_by_catalog = defaultdict(list)
    for record in analysis["records"]:
        for catalog_id in record.get("catalog_ids", []):
            records_by_catalog[catalog_id].append(record)
    for catalog in analysis["catalogs"]:
        related = records_by_catalog.get(catalog["catalog_id"], [])
        size, digest = _write_gzip(output_dir / catalog["index_file"], {
            "version": 1, "catalog_id": catalog["catalog_id"],
            "title": catalog["title"], "status": catalog["status"], "records": related,
        })
        files.append({"file": catalog["index_file"], "bytes": size, "sha256": digest, "items": len(related)})
    search_index_dir = output_dir / "search-index"
    search_index_dir.mkdir(parents=True, exist_ok=True)
    expected_search_files = set()
    for prefix, payload in search_shards.items():
        relative_path = "search-index/" + prefix + ".json.gz"
        expected_search_files.add(relative_path)
        size, digest = _write_gzip(output_dir / relative_path, payload)
        files.append({"file": relative_path, "bytes": size, "sha256": digest, "items": len(payload["tokens"])})
    for stale_path in search_index_dir.glob("*.json.gz"):
        relative_path = stale_path.relative_to(output_dir).as_posix()
        if relative_path not in expected_search_files:
            stale_path.unlink()

    size, digest = _write_gzip(output_dir / "records.json.gz", {
        "version": 1, "records": analysis["records"],
    })
    files.append({"file": "records.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["records"])})
    size, digest = _write_gzip(output_dir / "media-references.json.gz", {
        "version": 2, "references": analysis["media_references"],
        "unique_media_urls": analysis["media_urls"],
    })
    files.append({"file": "media-references.json.gz", "bytes": size, "sha256": digest, "items": len(analysis["media_references"])})
    snapshot_fields = {
        "analysis_started_at": analysis.get("analysis_started_at"),
        "analysis_finished_at": analysis.get("analysis_finished_at"),
        "persisted_snapshot_at": analysis.get("persisted_snapshot_at", analysis.get("generated_at")),
        "analysis_elapsed_seconds": analysis.get("analysis_elapsed_seconds"),
        "elapsed_wall_clock_seconds": analysis.get("elapsed_wall_clock_seconds"),
        "previous_counts": analysis.get("previous_counts"),
        "count_deltas": analysis.get("count_deltas"),
        "throughput": analysis.get("throughput"),
    }
    report = {
        "schema_version": 1, "analysis_version": analysis.get("analysis_version", ANALYZER_VERSION),
        "generated_at": analysis["generated_at"],
        "source_index_pages": analysis["source_index_pages"],
        "source_index_updated_at": analysis["source_index_updated_at"],
        "source_index_sha256": analysis.get("source_index_sha256"),
        "metrics": analysis["metrics"],
        "source_policy_review": analysis.get("source_policy_review"),
        "catalogs_verified": 0, "records_published": 0,
        **snapshot_fields,
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "version": 1, "analysis_version": analysis.get("analysis_version", ANALYZER_VERSION),
        "integrity": "sha256",
        "source_index_pages": analysis["source_index_pages"],
        "source_index_updated_at": analysis["source_index_updated_at"],
        "source_index_sha256": analysis.get("source_index_sha256"),
        "catalog_count": len(analysis["catalogs"]), "record_count": len(analysis["records"]),
        "generated_at": analysis["generated_at"],
        "metrics": analysis["metrics"],
        "source_policy_review": analysis.get("source_policy_review"),
        **snapshot_fields,
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
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    if manifest.get("metrics") != report.get("metrics"):
        raise ValueError("report and manifest metrics do not match")
    policy_review = manifest.get("source_policy_review")
    if (
        policy_review != report.get("source_policy_review")
        or not isinstance(policy_review, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(policy_review.get("register_sha256", "")))
        or policy_review.get("scope") != "source-level-only"
        or policy_review.get("record_level_clearance") != "not_reviewed"
        or policy_review.get("commercial_eligibility") is not False
        or policy_review.get("republication") != "not_cleared"
    ):
        raise ValueError("source policy review summary is missing or overstates record-level clearance")
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
    for page in pages:
        page_record_ids = page.get("record_ids", [])
        if not isinstance(page_record_ids, list) or len(page_record_ids) != len(set(page_record_ids)):
            raise ValueError("source page record relationships are malformed or duplicated")
        if page.get("record_id") and page["record_id"] not in page_record_ids:
            raise ValueError("primary page record is missing from its record relationship list")
        for related_record_id in page_record_ids:
            related_record = records_by_id.get(related_record_id)
            if related_record is None or page.get("page_id") not in related_record.get("source_page_ids", []):
                raise ValueError("source page record relationship lacks matching record provenance")
    for record in records:
        if stable_id("record", record.get("canonical_entity_key", "")) != record.get("record_id"):
            raise ValueError("record ID does not match its canonical entity key")
        if record.get("commercial_eligible") is not False or record.get("source_terms_status") != "not_reviewed":
            raise ValueError("candidate records cannot be marked commercially eligible or source-cleared")
        if (
            record.get("source_policy_review_status") not in (
                "not_reviewed", "conditional_source_review", "partial_source_review"
            )
            or record.get("record_level_clearance") != "not_reviewed"
        ):
            raise ValueError("source-level policy review cannot clear an individual record")
        if record.get("source_policy_review_status") != "not_reviewed" and (
            not record.get("source_policy_review_hosts")
            or not record.get("source_policy_review_evidence_urls")
            or record.get("source_policy_review_scope") != "source-level-only"
        ):
            raise ValueError("conditional source-policy annotations lack evidence or scope")
        if stable_id("entity", record.get("canonical_entity_key", "")) != record.get("entity_id"):
            raise ValueError("entity ID does not match its canonical entity key")
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
        if catalog.get("catalog_origin") not in ("source_declared_collection", "content_list_declared_collection", "structured_schema_collection", "derived_from_repeated_records"):
            raise ValueError("catalog origin is missing or invalid")
        if catalog.get("commercial_eligible") is not False or catalog.get("source_terms_status") != "not_reviewed":
            raise ValueError("candidate catalogs cannot be marked commercially eligible or source-cleared")
        if (
            catalog.get("source_policy_review_status") not in (
                "not_reviewed", "conditional_source_review", "partial_source_review"
            )
            or catalog.get("record_level_clearance") != "not_reviewed"
        ):
            raise ValueError("source-level policy review cannot clear an individual catalog")
        if catalog.get("source_policy_review_status") != "not_reviewed" and (
            not catalog.get("source_policy_review_hosts")
            or not catalog.get("source_policy_review_evidence_urls")
            or catalog.get("source_policy_review_scope") != "source-level-only"
        ):
            raise ValueError("conditional catalog policy annotations lack evidence or scope")
        if catalog.get("record_count") != len(catalog.get("record_ids", [])):
            raise ValueError("catalog record count does not match its membership")
        if catalog.get("source_page_count") != len(catalog.get("source_page_ids", [])):
            raise ValueError("catalog source-page count does not match its provenance")
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
    search_index_prefixes = set()
    search_index_tokens = set()
    search_index_postings = 0
    for entry in manifest["files"]:
        path = output_dir / entry["file"]
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("catalog analysis artifact integrity mismatch: " + entry["file"])
        payload = json.loads(gzip.decompress(raw).decode("utf-8"))
        if entry["file"].startswith("indexes/"):
            index_parts = Path(entry["file"]).parts
            if (
                len(index_parts) != 3
                or index_parts[0] != "indexes"
                or index_parts[1] != index_parts[2][:2]
                or not re.fullmatch(r"[0-9a-f]{24}\.json\.gz", index_parts[2])
            ):
                raise ValueError("per-catalog index path is not prefix-sharded")
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
        elif entry["file"].startswith("search-index/"):
            search_parts = Path(entry["file"]).parts
            if (
                len(search_parts) != 2
                or not re.fullmatch(r"[0-9a-f]{2}\.json\.gz", search_parts[1])
            ):
                raise ValueError("search index shard path is invalid")
            prefix = search_parts[1][:2]
            payload_tokens = payload.get("tokens")
            if (
                payload.get("version") != 1
                or payload.get("prefix") != prefix
                or not isinstance(payload_tokens, dict)
                or len(payload_tokens) != entry["items"]
                or prefix in search_index_prefixes
            ):
                raise ValueError("search index shard schema or item count is invalid")
            for token, related_catalog_ids in payload_tokens.items():
                if (
                    not isinstance(token, str)
                    or len(token) < 2
                    or token != unicodedata.normalize("NFKC", token).casefold()
                    or token in STOP_TOKENS
                    or hashlib.sha256(token.encode("utf-8")).hexdigest()[:2] != prefix
                    or token in search_index_tokens
                    or not isinstance(related_catalog_ids, list)
                    or not related_catalog_ids
                    or len(related_catalog_ids) != len(set(related_catalog_ids))
                    or related_catalog_ids != sorted(related_catalog_ids)
                    or any(catalog_id not in catalogs_by_id for catalog_id in related_catalog_ids)
                ):
                    raise ValueError("search index token postings are invalid")
                search_index_tokens.add(token)
                search_index_postings += len(related_catalog_ids)
            search_index_prefixes.add(prefix)
    if index_catalog_ids != set(catalog_ids):
        raise ValueError("one or more catalogs are missing a per-catalog index")
    metrics = manifest.get("metrics", {})
    if (
        not search_index_prefixes
        or metrics.get("search_index_tokens") != len(search_index_tokens)
        or metrics.get("search_index_shards") != len(search_index_prefixes)
        or metrics.get("search_index_catalog_postings") != search_index_postings
    ):
        raise ValueError("search index metrics do not match validated token postings")
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
        "search_index_shards_validated": len(search_index_prefixes),
        "search_index_tokens_validated": len(search_index_tokens),
        "search_index_catalog_postings_validated": search_index_postings,
    }


def _read_previous_snapshot(output_dir: Path) -> dict | None:
    report_path = output_dir / "report.json"
    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("analysis_version") != ANALYZER_VERSION:
            return None
        metrics = report.get("metrics", {})
        snapshot_at = report.get("persisted_snapshot_at") or report.get("generated_at")
        if not snapshot_at:
            return None
        counts = {
            key: metrics.get(key)
            for key in ("pages_analyzed", "eligible_pages", "unique_catalogs_discovered", "subcatalogs_discovered", "unique_records", "media_references")
        }
        if any(value is None for value in counts.values()):
            return None
        return {"snapshot_at": snapshot_at, "counts": counts}
    except (OSError, ValueError, json.JSONDecodeError):
        return None


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
    previous_snapshot = _read_previous_snapshot(args.output_dir)
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
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
    finished_at = datetime.now(timezone.utc)
    finished_iso = finished_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    analysis["generated_at"] = finished_iso
    analysis["analysis_started_at"] = started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    analysis["analysis_finished_at"] = finished_iso
    analysis["persisted_snapshot_at"] = finished_iso
    analysis["analysis_elapsed_seconds"] = round(time.monotonic() - started_monotonic, 3)
    if previous_snapshot:
        previous_at = datetime.fromisoformat(previous_snapshot["snapshot_at"].replace("Z", "+00:00"))
        wall_seconds = max(0.001, (finished_at - previous_at).total_seconds())
        current_counts = {key: analysis["metrics"].get(key, 0) for key in previous_snapshot["counts"]}
        deltas = {key: current_counts[key] - previous_snapshot["counts"][key] for key in current_counts}
        minutes = wall_seconds / 60.0
        analysis["previous_counts"] = previous_snapshot["counts"]
        analysis["count_deltas"] = deltas
        analysis["elapsed_wall_clock_seconds"] = round(wall_seconds, 3)
        analysis["throughput"] = {
            "pages_per_minute": round(deltas["pages_analyzed"] / minutes, 4),
            "catalogs_per_minute": round(deltas["unique_catalogs_discovered"] / minutes, 4),
            "records_per_minute": round(deltas["unique_records"] / minutes, 4),
            "subcatalogs_per_minute": round(deltas["subcatalogs_discovered"] / minutes, 4),
            "media_references_per_minute": round(deltas["media_references"] / minutes, 4),
            "measurement": "persisted_count_delta_over_wall_clock_interval",
        }
    else:
        analysis["previous_counts"] = None
        analysis["count_deltas"] = None
        analysis["elapsed_wall_clock_seconds"] = None
        analysis["throughput"] = None
    manifest = write_outputs(analysis, args.output_dir)
    print(json.dumps({
        "metrics": analysis["metrics"],
        "previous_counts": analysis["previous_counts"],
        "count_deltas": analysis["count_deltas"],
        "elapsed_wall_clock_seconds": analysis["elapsed_wall_clock_seconds"],
        "analysis_elapsed_seconds": analysis["analysis_elapsed_seconds"],
        "throughput": analysis["throughput"],
        "catalog_index_files": manifest["catalog_count"],
        "files": len(manifest["files"]),
    }, indent=2))
    for query in (args.query or ["Rolex", "Las Vegas"]):
        print(json.dumps({
            "query": query,
            "results": search_catalog_network(query, analysis),
            "source_page_evidence": search_source_page_evidence(query, index.get("pages", []), limit=5),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
