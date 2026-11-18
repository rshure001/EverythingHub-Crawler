"""Metadata-only multimodal intelligence for Everything Hub.

Normalizes permitted media-discovery records, keeps provenance and reuse rights
visible, and ranks existing records locally. It performs no network requests,
downloads, transcription, transcoding, filesystem writes, or publishing.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

MEDIA_TYPES = ("artwork", "audio", "video", "design")
MAX_QUERY_CHARS = 240
MAX_RESULTS = 50

_TYPE_ALIASES = {
    "image": "artwork", "images": "artwork", "photo": "artwork",
    "photograph": "artwork", "illustration": "artwork", "art": "artwork",
    "artwork": "artwork",
    "sound": "audio", "music": "audio", "recording": "audio",
    "podcast": "audio", "audio": "audio",
    "film": "video", "movie": "video", "clip": "video", "video": "video",
    "graphic": "design", "template": "design", "mockup": "design",
    "vector": "design", "font": "design", "layout": "design",
    "wireframe": "design", "design system": "design", "ui kit": "design",
    "design": "design",
}
_DESIGN_KINDS = {
    "template", "mockup", "vector", "font", "layout", "wireframe",
    "design system", "ui kit", "icon set", "brand kit", "presentation",
}
_EXTENSIONS = {
    "artwork": {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"},
    "audio": {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav"},
    "video": {".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"},
    "design": {".ai", ".eps", ".fig", ".indd", ".otf", ".psd", ".sketch", ".ttf", ".woff", ".woff2", ".xd"},
}
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_TAG_RE = re.compile(r"<[^>]*>")


def tokenize(value: Any) -> list[str]:
    """Return stable Unicode-aware search tokens."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _TOKEN_RE.findall(normalized)


def _safe_http_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        _ = parsed.port  # Reject malformed ports.
        return raw
    except (TypeError, ValueError):
        return None


def _first_text(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_text(value: Any, limit: int = 1200) -> str:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value if item is not None)
    return _TAG_RE.sub(" ", str(value or "")).replace("\x00", "").strip()[:limit]


def _as_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1", "required"}:
            return True
        if lowered in {"false", "no", "0", "not required"}:
            return False
    return None


def _infer_media_type(record: Mapping[str, Any], candidate_url: str) -> str | None:
    explicit = _first_text(record, "media_type", "mediaType", "type", "kind").casefold()
    if explicit in _TYPE_ALIASES:
        return _TYPE_ALIASES[explicit]

    asset_kind = _first_text(record, "asset_kind", "assetKind", "format_kind").casefold()
    if asset_kind in _DESIGN_KINDS:
        return "design"

    mime = _first_text(record, "mime_type", "mimeType", "mime", "content_type").casefold()
    if mime.startswith("image/"):
        return "artwork"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"

    try:
        suffix = urlsplit(candidate_url).path.rsplit("/", 1)[-1].rsplit(".", 1)
        extension = "." + suffix[-1].casefold() if len(suffix) == 2 else ""
    except ValueError:
        extension = ""
    for media_type, extensions in _EXTENSIONS.items():
        if extension in extensions:
            return media_type
    return None


def normalize_media_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one provider record; reject unsafe URLs and robots-denied items.

    A source page is mandatory for provenance. Audio requires a direct audio URL.
    Images, video and design assets may use a preview when no direct file URL is
    supplied, but the returned source page remains the click-through destination.
    Rights metadata is never treated as legal approval for reuse.
    """
    if not isinstance(record, Mapping) or record.get("robots_allowed") is False:
        return None

    source_url = _safe_http_url(_first_text(
        record, "pageUrl", "page_url", "source_url", "sourceUrl", "source_page_url"
    ))
    if not source_url:
        return None

    media_candidate = _first_text(
        record, "media_url", "mediaUrl", "imageUrl", "audioUrl", "videoUrl",
        "designUrl", "asset_url", "assetUrl",
    )
    if not media_candidate:
        possible_url = _first_text(record, "url")
        if possible_url and possible_url != source_url:
            media_candidate = possible_url

    preview_candidate = _first_text(
        record, "preview_url", "previewUrl", "thumbnailUrl", "thumbnail_url",
        "posterUrl", "imageUrl",
    )
    media_type = _infer_media_type(record, media_candidate or preview_candidate)
    if media_type not in MEDIA_TYPES:
        return None

    media_url = _safe_http_url(media_candidate) if media_candidate else None
    preview_url = _safe_http_url(preview_candidate) if preview_candidate else None

    if media_type == "audio" and not media_url:
        return None
    if media_type == "artwork" and not (media_url or preview_url):
        return None
    if media_type in {"video", "design"} and not (media_url or preview_url):
        return None
    if media_type == "artwork" and not preview_url:
        preview_url = media_url

    title = _as_text(_first_text(record, "title", "name", "filename", "file_name"), 500)
    if not title:
        title = (urlsplit(media_url or preview_url or source_url).path.rsplit("/", 1)[-1] or
                 urlsplit(source_url).hostname or "Untitled media")
    description = _as_text(record.get("description"), 1200)
    tags_value = record.get("tags", [])
    tags = (
        [_as_text(item, 80) for item in tags_value if _as_text(item, 80)]
        if isinstance(tags_value, (list, tuple, set))
        else [_as_text(tags_value, 240)] if _as_text(tags_value, 240) else []
    )

    license_name = _as_text(record.get("license"), 200)
    license_url = _safe_http_url(record.get("licenseUrl") or record.get("license_url"))
    usage_rights = _as_text(
        record.get("usageRights") or record.get("usage_rights"),
        500,
    ) or "Not assessed — verify the source page terms before reuse."
    rights_status = (
        "review_required"
        if license_name and license_url and "unverified" not in usage_rights.casefold()
        else "unverified"
    )

    return {
        "media_type": media_type,
        "title": title,
        "description": description,
        "tags": tags,
        "source": _as_text(record.get("source") or record.get("provider"), 160) or "Unknown source",
        "source_page_url": source_url,
        "media_url": media_url,
        "preview_url": preview_url,
        "mime_type": _as_text(record.get("mimeType") or record.get("mime_type") or record.get("mime"), 120),
        "license": license_name or None,
        "license_url": license_url,
        "creator": _as_text(record.get("creator") or record.get("artist"), 300) or None,
        "credit": _as_text(record.get("credit"), 500) or None,
        "attribution_required": _as_bool_or_none(
            record.get("attributionRequired", record.get("attribution_required"))
        ),
        "usage_rights": usage_rights,
        "rights_status": rights_status,
        "policy_status": "robots_allowed" if record.get("robots_allowed") is True else "not_checked",
    }


def rank_media(
    records: Iterable[Mapping[str, Any]],
    query: str,
    *,
    media_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Rank normalized media metadata locally; never fetch or inspect asset bytes."""
    raw_query = str(query or "").strip()[:MAX_QUERY_CHARS]
    terms = set(tokenize(raw_query))
    if not terms:
        return []

    selected_type = _TYPE_ALIASES.get(str(media_type or "").casefold(), media_type)
    if selected_type is not None and selected_type not in MEDIA_TYPES:
        return []

    safe_limit = max(1, min(int(limit), MAX_RESULTS))
    ranked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for record in records:
        normalized = normalize_media_record(record)
        if not normalized:
            continue
        if selected_type and normalized["media_type"] != selected_type:
            continue

        identity = (normalized["media_type"], normalized["media_url"] or normalized["preview_url"] or "")
        if identity in seen:
            continue
        seen.add(identity)

        fields = {
            "title": tokenize(normalized["title"]),
            "tags": tokenize(" ".join(normalized["tags"])),
            "description": tokenize(normalized["description"]),
            "creator": tokenize(normalized["creator"] or ""),
        }
        weights = {"title": 4.0, "tags": 2.5, "description": 1.5, "creator": 1.0}
        score = sum(
            weights[field] * sum(tokens.count(term) for term in terms)
            for field, tokens in fields.items()
        )
        if score <= 0:
            continue
        phrase = " ".join(tokenize(raw_query))
        title_phrase = " ".join(fields["title"])
        if phrase and phrase in title_phrase:
            score += 3.0
        ranked.append({**normalized, "relevance_score": round(score, 6)})

    ranked.sort(key=lambda item: (
        -item["relevance_score"],
        item["title"].casefold(),
        item["source_page_url"],
    ))
    return ranked[:safe_limit]


def media_inventory(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize accepted modality and rights metadata without exposing asset content."""
    modalities = Counter()
    rights = Counter()
    accepted = 0
    rejected = 0
    for record in records:
        normalized = normalize_media_record(record)
        if normalized is None:
            rejected += 1
            continue
        accepted += 1
        modalities[normalized["media_type"]] += 1
        rights[normalized["rights_status"]] += 1
    return {
        "accepted": accepted,
        "rejected": rejected,
        "by_modality": {kind: modalities.get(kind, 0) for kind in MEDIA_TYPES},
        "by_rights_status": {
            "review_required": rights.get("review_required", 0),
            "unverified": rights.get("unverified", 0),
        },
    }
