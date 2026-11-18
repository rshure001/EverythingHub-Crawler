"""Hurricane Core multimodal intelligence primitives.

Normalizes image, audio, video, and design metadata into a shared searchable
asset record. Extractors may supply verified metadata and derivatives. This
module performs no media decoding, external requests, or source-file mutations.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

MEDIA_TYPES = {"image", "audio", "video", "design"}
MAX_TEXT, MAX_TAGS, MAX_DURATION_SECONDS = 12000, 80, 86400
TEXT_FIELDS = (
    "title", "description", "alt_text", "transcript", "ocr_text",
    "scene_descriptions", "design_text", "creator", "collection",
)


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value[:200])
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return set(re.findall(r"[\w-]{2,}", normalized, flags=re.UNICODE))


def _url(value: Any) -> str:
    """Reject unsafe URL forms and non-public IP literals; fetches must still use safe_http."""
    raw = _text(value, 2048)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
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
        return raw
    except (TypeError, ValueError):
        return ""


def is_safe_public_url(value: Any) -> bool:
    """Return whether a URL passes the core's conservative URL checks."""
    return bool(_url(value))


def _number(value: Any, maximum: float = 1_000_000) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return max(0.0, min(maximum, number))


def _tags(value: Any) -> list[str]:
    values = (
        re.split(r"[,;|]", value)
        if isinstance(value, str)
        else value if isinstance(value, (list, tuple, set)) else []
    )
    output, seen = [], set()
    for item in values:
        tag = _text(item, 80)
        key = tag.casefold()
        if tag and key not in seen:
            output.append(tag)
            seen.add(key)
        if len(output) >= MAX_TAGS:
            break
    return output


def _is_explicit_false(value: Any) -> bool:
    if value is False or (not isinstance(value, bool) and value == 0):
        return True
    return isinstance(value, str) and value.strip().casefold() in {
        "false", "no", "0", "blocked", "disallowed", "denied",
    }


def _is_explicit_true(value: Any) -> bool:
    if value is True or (not isinstance(value, bool) and value == 1):
        return True
    return isinstance(value, str) and value.strip().casefold() in {
        "true", "yes", "1", "noindex", "none",
    }


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1", "required"}:
            return True
        if lowered in {"false", "no", "0", "not required"}:
            return False
    return None


def _dedupe_key(asset: Mapping[str, Any]) -> tuple[str, str, str]:
    def canonical(value: Any) -> str:
        return _text(value).split("#", 1)[0].rstrip("/").casefold()
    return (
        str(asset.get("media_type") or ""),
        canonical(asset.get("source_page_url") or asset.get("url")),
        canonical(asset.get("asset_url") or asset.get("url")),
    )


def normalize_asset(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize media metadata and provenance; never infer reuse permission."""
    if not isinstance(record, Mapping):
        raise ValueError("asset record must be a mapping")
    if (
        _is_explicit_false(record.get("robots_allowed"))
        or _is_explicit_false(record.get("indexing_allowed"))
        or _is_explicit_true(record.get("noindex"))
    ):
        raise ValueError("source policy excludes this asset")

    kind = _text(
        record.get("media_type") or record.get("mediaType") or record.get("type"), 20
    ).casefold()
    if kind == "artwork":
        kind = "image"
    if kind not in MEDIA_TYPES:
        raise ValueError("media_type must be image, artwork, audio, video, or design")

    raw_page = (
        record.get("source_page_url") or record.get("pageUrl")
        or record.get("page_url") or record.get("source_url")
    )
    asset_fields = {
        "image": ("asset_url", "media_url", "imageUrl", "image_url"),
        "audio": ("asset_url", "media_url", "audioUrl", "audio_url"),
        "video": ("asset_url", "media_url", "videoUrl", "video_url"),
        "design": ("asset_url", "media_url", "designUrl", "design_url"),
    }[kind]
    raw_asset = next(
        (record.get(field) for field in asset_fields
         if isinstance(record.get(field), str) and record.get(field).strip()),
        None,
    )
    raw_url = record.get("url")
    source_page_url = _url(raw_page) if raw_page else ""
    asset_url = _url(raw_asset) if raw_asset else ""
    url = _url(raw_url) or source_page_url or asset_url
    if not url:
        raise ValueError("asset requires a valid public HTTP(S) URL")

    if not source_page_url:
        source_page_url = _url(raw_url) or asset_url or url
    if not asset_url and not raw_page:
        asset_url = _url(raw_url) or url

    raw_thumbnail = (
        record.get("thumbnail_url") or record.get("thumbnailUrl")
        or record.get("preview_url") or record.get("previewUrl")
        or (record.get("imageUrl") if kind in {"image", "video"} else None)
        or (asset_url if kind == "image" else None)
    )
    thumbnail_url = _url(raw_thumbnail)

    fields = {key: _text(record.get(key)) for key in TEXT_FIELDS}
    tags = _tags(record.get("tags"))
    width = _number(record.get("width"), 100000)
    height = _number(record.get("height"), 100000)
    duration = _number(record.get("duration_seconds"), MAX_DURATION_SECONDS)
    rate = _number(record.get("sample_rate"), 768000)
    channels = _number(record.get("channels"), 256)
    fps = _number(record.get("frame_rate"), 1000)
    frames = _number(record.get("frame_count"), 10000000)

    license_name = _text(record.get("license") or record.get("license_name"), 300)
    rights = _text(record.get("usage_rights") or record.get("usageRights"), 300) or license_name
    license_url = _url(record.get("license_url") or record.get("licenseUrl"))
    credit = _text(record.get("credit"), 500)
    attribution = _text(record.get("attribution") or record.get("creator") or credit, 500)
    attribution_required = _bool_or_none(
        record.get("attribution_required") or record.get("attributionRequired")
    )
    status = _text(record.get("rights_status"), 30).casefold()
    if status not in {"verified", "unverified", "restricted", "unknown"}:
        status = "unknown"
    if status == "verified" and (not rights or not license_url):
        status = "unknown"

    searchable = " ".join(
        value for value in (
            fields["title"], fields["description"], fields["alt_text"],
            fields["transcript"], fields["ocr_text"], fields["scene_descriptions"],
            fields["design_text"], fields["creator"], fields["collection"],
            " ".join(tags),
        ) if value
    )
    policy_status = (
        "robots_allowed"
        if _is_explicit_true(record.get("robots_allowed"))
        else "not_checked"
    )
    return {
        "asset_id": _text(record.get("asset_id") or record.get("id"), 160),
        "media_type": kind,
        "url": url,
        "source_page_url": source_page_url,
        "asset_url": asset_url,
        "thumbnail_url": thumbnail_url,
        **fields,
        "tags": tags,
        "search_text": searchable[:MAX_TEXT],
        "width": int(width) if width is not None else None,
        "height": int(height) if height is not None else None,
        "duration_seconds": duration,
        "sample_rate": int(rate) if rate is not None else None,
        "channels": int(channels) if channels is not None else None,
        "frame_rate": fps,
        "frame_count": int(frames) if frames is not None else None,
        "usage_rights": rights or "Unknown — verify on the original source",
        "license": license_name or None,
        "license_url": license_url,
        "attribution": attribution,
        "credit": credit or None,
        "attribution_required": attribution_required,
        "rights_status": status,
        "policy_status": policy_status,
        "source": _text(record.get("source") or record.get("provider"), 200),
        "published_at": _text(record.get("published_at"), 80),
        "extraction": {
            "transcript_available": bool(fields["transcript"]),
            "ocr_available": bool(fields["ocr_text"]),
            "scene_descriptions_available": bool(fields["scene_descriptions"]),
            "design_text_available": bool(fields["design_text"]),
        },
    }


def normalize_discovery_result(record: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt the deployed image/video discovery response to Hurricane Core."""
    if not isinstance(record, Mapping):
        raise ValueError("discovery result must be a mapping")
    kind = _text(
        record.get("media_type") or record.get("mediaType") or record.get("type"), 20
    ).casefold()
    if kind == "artwork":
        kind = "image"
    if kind not in MEDIA_TYPES:
        raise ValueError("unsupported discovery media type")

    page_url = (
        record.get("source_page_url") or record.get("pageUrl")
        or record.get("page_url") or record.get("source_url")
    )
    direct_fields = {
        "image": ("imageUrl", "image_url", "asset_url"),
        "audio": ("audioUrl", "audio_url", "asset_url"),
        "video": ("videoUrl", "video_url", "asset_url"),
        "design": ("designUrl", "design_url", "asset_url"),
    }[kind]
    direct_url = next(
        (record.get(field) for field in direct_fields
         if isinstance(record.get(field), str) and record.get(field).strip()),
        None,
    )
    canonical_url = page_url or direct_url or record.get("url")
    if not canonical_url:
        raise ValueError("discovery result requires a source page or asset URL")

    usage_rights = record.get("usageRights") or record.get("usage_rights") or record.get("license")
    rights_status = record.get("rights_status")
    if not rights_status:
        rights_status = (
            "unverified"
            if "unverified" in _text(usage_rights, 500).casefold()
            else "unknown"
        )
    mapped = {
        **record,
        "type": kind,
        "url": canonical_url,
        "source_page_url": page_url or canonical_url,
        "asset_url": direct_url,
        "thumbnail_url": (
            record.get("thumbnailUrl") or record.get("thumbnail_url")
            or (record.get("imageUrl") if kind == "video" else None)
        ),
        "usage_rights": usage_rights,
        "license_url": record.get("licenseUrl") or record.get("license_url"),
        "attribution": (
            record.get("attribution") or record.get("credit") or record.get("creator")
        ),
        "rights_status": rights_status,
        "source": record.get("source") or record.get("provider"),
    }
    return normalize_asset(mapped)


def index_assets(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and deduplicate by modality, source page, and asset URL."""
    output, seen = [], set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        try:
            asset = normalize_asset(record)
        except ValueError:
            continue
        key = _dedupe_key(asset)
        if key not in seen:
            output.append(asset)
            seen.add(key)
    return output


def search_assets(
    query: str,
    assets: Iterable[Mapping[str, Any]],
    *,
    media_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search captions, transcripts, OCR, scene notes, tags, and design text."""
    terms = _tokens(_text(query, 500))
    if not terms:
        return []
    selected = _text(media_type, 20).casefold() if media_type else None
    if selected == "artwork":
        selected = "image"
    if selected is not None and selected not in MEDIA_TYPES:
        raise ValueError("media_type must be image, artwork, audio, video, or design")
    try:
        safe_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError, OverflowError):
        safe_limit = 20

    results, seen = [], set()
    for raw in assets:
        try:
            asset = normalize_asset(raw)
        except (ValueError, TypeError, AttributeError):
            continue
        if selected and asset["media_type"] != selected:
            continue
        key = _dedupe_key(asset)
        if key in seen:
            continue
        seen.add(key)
        fields = {
            "title": _tokens(asset["title"]),
            "tags": _tokens(" ".join(asset["tags"])),
            "description": _tokens(asset["description"]),
            "transcript": _tokens(asset["transcript"]),
            "scenes": _tokens(asset["scene_descriptions"]),
            "ocr": _tokens(asset["ocr_text"]),
            "design": _tokens(asset["design_text"]),
            "other": _tokens(" ".join((
                asset["alt_text"], asset["creator"], asset["collection"],
            ))),
        }
        if not terms.issubset(set().union(*fields.values())):
            continue
        score = sum(
            len(terms & fields[name]) * weight
            for name, weight in (
                ("title", 8), ("tags", 6), ("description", 4),
                ("transcript", 3), ("scenes", 3), ("ocr", 3),
                ("design", 3), ("other", 1),
            )
        )
        results.append({**asset, "match_score": score})
    results.sort(key=lambda item: (-item["match_score"], item["url"]))
    return results[:safe_limit]
