"""Bounded, metadata-only media discovery from already fetched HTML pages.

This module never downloads media files or infers reuse rights. The crawler
must separately enforce same-origin and robots policy before persisting results.
"""
from __future__ import annotations

import ipaddress
import re
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_MEDIA_PER_PAGE = 12
MAX_MEDIA_URL_LENGTH = 2048
MAX_TEXT_LENGTH = 500
MAX_STORED_CANDIDATES = 50000
MAX_SOURCE_PAGES = 5
MEDIA_TYPES = {"image", "audio", "video"}
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
SENSITIVE_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "auth", "authorization", "credential",
    "expires", "jwt", "key", "password", "policy", "secret", "session",
    "sig", "signature", "token", "x-amz-credential", "x-amz-date",
    "x-amz-expires", "x-amz-security-token", "x-amz-signature",
    "x-goog-algorithm", "x-goog-credential", "x-goog-date",
    "x-goog-expires", "x-goog-signature", "se", "skoid", "sktid",
    "skt", "ske", "sks", "skv", "sip", "sp", "spr", "sr", "st", "sv",
}


def _text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def safe_media_url(base_url: str, value: Any) -> str:
    """Resolve a public HTTP(S) URL while rejecting credentials and signed URLs."""
    raw_value = str(value or "").strip()
    if not raw_value or len(raw_value) > MAX_MEDIA_URL_LENGTH:
        return ""
    raw = _text(raw_value, MAX_MEDIA_URL_LENGTH)
    if not raw:
        return ""
    try:
        absolute = urljoin(base_url, raw)
        parsed = urlsplit(absolute)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        _ = parsed.port
        host = parsed.hostname.casefold().rstrip(".")
        if host in {"localhost", "localhost.localdomain"}:
            return ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return ""
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        keys = {key.casefold() for key, _ in pairs}
        if any(
            key in SENSITIVE_QUERY_KEYS
            or key.startswith("x-amz-")
            or key.startswith("x-goog-")
            or key.startswith("x-ms-")
            for key in keys
        ):
            return ""
        query = urlencode([
            (key, item) for key, item in pairs
            if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_KEYS
        ])
        return urlunsplit((
            parsed.scheme.casefold(), parsed.netloc.casefold(),
            parsed.path or "/", query, "",
        ))
    except (TypeError, ValueError):
        return ""


def _srcset(value: str) -> Iterable[str]:
    for item in str(value or "").split(","):
        fields = item.strip().split()
        if fields:
            yield fields[0]


class _MediaParser(HTMLParser):
    def __init__(self, page_url: str, page_title: str, description: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.page_title = _text(page_title, 300)
        self.description = _text(description, 500)
        self.context: list[tuple[str, str]] = []
        self.candidates: list[dict[str, str]] = []
        self.seen: dict[tuple[str, str], int] = {}

    def add(self, raw_url: Any, media_type: str, alt: Any = "",
            title: Any = "", thumbnail_url: Any = "") -> None:
        if media_type not in MEDIA_TYPES:
            return
        url = safe_media_url(self.page_url, raw_url)
        if not url:
            return
        thumbnail = safe_media_url(self.page_url, thumbnail_url) if thumbnail_url else ""
        key = (media_type, url)
        if key in self.seen:
            row = self.candidates[self.seen[key]]
            row["alt_text"] = row["alt_text"] or _text(alt)
            row["title"] = row["title"] or _text(title, 300) or self.page_title
            row["thumbnail_url"] = row["thumbnail_url"] or thumbnail
            return
        if len(self.candidates) >= MAX_MEDIA_PER_PAGE:
            return
        self.seen[key] = len(self.candidates)
        self.candidates.append({
            "media_type": media_type,
            "url": url,
            "source_page_url": self.page_url,
            "title": _text(title, 300) or self.page_title,
            "alt_text": _text(alt),
            "thumbnail_url": thumbnail,
        })

    def add_srcset(self, value: Any, media_type: str, alt: Any = "", title: Any = "") -> None:
        for url in _srcset(str(value or "")):
            self.add(url, media_type, alt, title)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag == "meta":
            prop = _text(values.get("property"), 80).casefold()
            name = _text(values.get("name"), 80).casefold()
            itemprop = _text(values.get("itemprop"), 80).casefold()
            content = values.get("content", "")
            key = prop or name or itemprop
            if key in {"og:image", "og:image:url", "og:image:secure_url",
                       "twitter:image", "twitter:image:src", "image_src", "thumbnailurl"}:
                self.add(content, "image")
            elif key in {"og:video", "og:video:url", "og:video:secure_url",
                         "twitter:player:stream"}:
                self.add(content, "video")
            elif key in {"og:audio", "og:audio:url", "og:audio:secure_url"}:
                self.add(content, "audio")
        elif tag == "link":
            rel = {part.casefold() for part in values.get("rel", "").split()}
            if "image_src" in rel:
                self.add(values.get("href"), "image")
        elif tag == "img":
            alt = values.get("alt", "")
            title = values.get("title", "")
            self.add(values.get("src") or values.get("data-src") or values.get("data-original"),
                     "image", alt, title)
            self.add_srcset(values.get("srcset") or values.get("data-srcset"), "image", alt, title)
        elif tag == "video":
            poster = values.get("poster", "")
            self.add(values.get("src"), "video", title=values.get("title", ""),
                     thumbnail_url=poster)
            if poster:
                self.add(poster, "image", alt="Video poster", title=values.get("title", ""))
            self.context.append(("video", poster))
        elif tag == "audio":
            self.add(values.get("src"), "audio", title=values.get("title", ""))
            self.context.append(("audio", ""))
        elif tag == "picture":
            self.context.append(("picture", ""))
        elif tag == "source":
            parent_entry = next((item for item in reversed(self.context)
                                 if item[0] in {"video", "audio", "picture"}), ("", ""))
            parent, parent_poster = parent_entry
            mime = values.get("type", "").casefold()
            if parent == "video" or mime.startswith("video/"):
                media_type = "video"
            elif parent == "audio" or mime.startswith("audio/"):
                media_type = "audio"
            elif parent == "picture" or mime.startswith("image/"):
                media_type = "image"
            else:
                media_type = ""
            if media_type:
                self.add(values.get("src"), media_type,
                         thumbnail_url=parent_poster if media_type == "video" else "")
                if media_type == "image":
                    self.add_srcset(values.get("srcset"), "image")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        matches = [index for index, item in enumerate(self.context) if item[0] == tag]
        if matches:
            del self.context[matches[-1]:]


def discover_media(html: str, page_url: str, page_title: str = "",
                   description: str = "") -> list[dict[str, str]]:
    """Extract at most 12 safe media references from one fetched HTML document."""
    source = safe_media_url("", page_url)
    if not source or not isinstance(html, str):
        return []
    parser = _MediaParser(source, page_title, description)
    parser.feed(html[:2_000_000])
    parser.close()
    return parser.candidates


def merge_media_candidates(existing: Iterable[Mapping[str, Any]],
                           incoming: Iterable[Mapping[str, Any]],
                           now: int,
                           limit: int = MAX_STORED_CANDIDATES) -> tuple[list[dict[str, Any]], int]:
    """Add deduplicated, policy-checked candidates without deleting prior records.

    robots_checked_at is copied from the timestamp captured immediately after
    the candidate media URL passed the current robots policy check. Run-end
    time is used only for catalog seen metadata, never as a substitute check.
    """
    observed_at = int(now)
    merged: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for raw in existing:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("media_type") or "")
        url = safe_media_url("", raw.get("url"))
        if kind not in MEDIA_TYPES or not url:
            continue
        key = (kind, url)
        if key in positions:
            continue
        row = dict(raw)
        row["url"] = url
        row["media_type"] = kind
        row.setdefault("seen_count", 1)
        row.setdefault("source_pages", [row.get("source_page_url", "")] if row.get("source_page_url") else [])
        positions[key] = len(merged)
        merged.append(row)

    added = 0
    for raw in incoming:
        if not isinstance(raw, Mapping) or raw.get("robots_allowed") is not True:
            continue
        kind = str(raw.get("media_type") or "")
        url = safe_media_url("", raw.get("url"))
        source_page = safe_media_url("", raw.get("source_page_url"))
        robots_checked_at = raw.get("robots_checked_at")
        if (
            kind not in MEDIA_TYPES
            or not url
            or not source_page
            or isinstance(robots_checked_at, bool)
            or not isinstance(robots_checked_at, int)
            or robots_checked_at <= 0
            or robots_checked_at > observed_at
        ):
            continue
        key = (kind, url)
        if key in positions:
            row = merged[positions[key]]
            row["seen_count"] = min(1_000_000, int(row.get("seen_count", 1) or 1) + 1)
            row["last_seen_at"] = observed_at
            row["robots_allowed"] = True
            row["robots_checked_at"] = robots_checked_at
            row["policy_status"] = "robots_allowed_not_fetched"
            raw_sources = row.get("source_pages")
            sources = list(raw_sources) if isinstance(raw_sources, (list, tuple)) else []
            if not sources and row.get("source_page_url"):
                sources = [row["source_page_url"]]
            if source_page not in sources and len(sources) < MAX_SOURCE_PAGES:
                sources.append(source_page)
            row["source_pages"] = sources
            for field in ("title", "alt_text", "thumbnail_url"):
                if not row.get(field) and raw.get(field):
                    row[field] = _text(raw[field], 2048)
            continue
        if len(merged) >= limit:
            continue
        row = {
            "media_type": kind,
            "url": url,
            "source_page_url": source_page,
            "source_pages": [source_page],
            "title": _text(raw.get("title"), 300),
            "alt_text": _text(raw.get("alt_text")),
            "thumbnail_url": safe_media_url("", raw.get("thumbnail_url")) if raw.get("thumbnail_url") else "",
            "robots_allowed": True,
            "robots_checked_at": robots_checked_at,
            "policy_status": "robots_allowed_not_fetched",
            "rights_status": "unknown",
            "usage_rights": "Unknown",
            "first_seen_at": observed_at,
            "last_seen_at": observed_at,
            "seen_count": 1,
        }
        positions[key] = len(merged)
        merged.append(row)
        added += 1
    return merged, added
