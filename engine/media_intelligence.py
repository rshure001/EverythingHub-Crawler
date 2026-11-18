"""Hurricane Core multimodal intelligence primitives.

Normalizes image, audio, video, and design metadata into a shared, searchable
asset record. Extractors may supply verified metadata and derivatives. No media
decoding, external requests, or source-file mutations occur in this module.
"""
from __future__ import annotations
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

MEDIA_TYPES = {"image", "audio", "video", "design"}
MAX_TEXT, MAX_TAGS, MAX_DURATION_SECONDS = 12000, 80, 86400
TEXT_FIELDS = ("title", "description", "alt_text", "transcript", "ocr_text",
               "scene_descriptions", "design_text", "creator", "collection")

def _text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None: return ""
    if isinstance(value, (list, tuple)): value = " ".join(str(x) for x in value[:200])
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]

def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[\w-]{2,}", value.casefold(), flags=re.UNICODE))

def _url(value: Any) -> str:
    raw = _text(value, 2048)
    try:
        p = urlsplit(raw)
        if p.scheme.casefold() not in {"http", "https"} or not p.hostname or p.username or p.password: return ""
        return raw
    except ValueError: return ""

def _number(value: Any, maximum: float = 1_000_000) -> float | None:
    try: n = float(value)
    except (TypeError, ValueError, OverflowError): return None
    if n != n or n in (float("inf"), float("-inf")): return None
    return max(0.0, min(maximum, n))

def _tags(value: Any) -> list[str]:
    values = re.split(r"[,;|]", value) if isinstance(value, str) else value if isinstance(value, (list, tuple, set)) else []
    out, seen = [], set()
    for item in values:
        tag = _text(item, 80); key = tag.casefold()
        if tag and key not in seen: out.append(tag); seen.add(key)
        if len(out) >= MAX_TAGS: break
    return out

def normalize_asset(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize media metadata; never infer reuse permission."""
    kind = _text(record.get("media_type") or record.get("type"), 20).casefold()
    if kind not in MEDIA_TYPES: raise ValueError("media_type must be image, audio, video, or design")
    url = _url(record.get("url") or record.get("asset_url"))
    if not url: raise ValueError("asset requires a valid public HTTP(S) URL")
    fields = {key: _text(record.get(key)) for key in TEXT_FIELDS}
    tags = _tags(record.get("tags"))
    width, height = _number(record.get("width"), 100000), _number(record.get("height"), 100000)
    duration = _number(record.get("duration_seconds"), MAX_DURATION_SECONDS)
    rate, channels = _number(record.get("sample_rate"), 768000), _number(record.get("channels"), 256)
    fps, frames = _number(record.get("frame_rate"), 1000), _number(record.get("frame_count"), 10000000)
    rights = _text(record.get("usage_rights") or record.get("license"), 300)
    license_url = _url(record.get("license_url"))
    attribution = _text(record.get("attribution") or record.get("creator"), 500)
    status = _text(record.get("rights_status"), 30).casefold()
    if status not in {"verified", "unverified", "restricted", "unknown"}: status = "unknown"
    if status == "verified" and not rights: status = "unknown"
    searchable = " ".join(v for v in (
        fields["title"], fields["description"], fields["alt_text"], fields["transcript"],
        fields["ocr_text"], fields["scene_descriptions"], fields["design_text"],
        fields["creator"], fields["collection"], " ".join(tags)) if v)
    return {
        "asset_id": _text(record.get("asset_id") or record.get("id"), 160),
        "media_type": kind, "url": url, "thumbnail_url": _url(record.get("thumbnail_url")),
        **fields, "tags": tags, "search_text": searchable[:MAX_TEXT],
        "width": int(width) if width is not None else None,
        "height": int(height) if height is not None else None,
        "duration_seconds": duration, "sample_rate": int(rate) if rate is not None else None,
        "channels": int(channels) if channels is not None else None,
        "frame_rate": fps, "frame_count": int(frames) if frames is not None else None,
        "usage_rights": rights or "Unknown — verify on the original source",
        "license_url": license_url, "attribution": attribution, "rights_status": status,
        "source": _text(record.get("source"), 200), "published_at": _text(record.get("published_at"), 80),
        "extraction": {"transcript_available": bool(fields["transcript"]),
            "ocr_available": bool(fields["ocr_text"]),
            "scene_descriptions_available": bool(fields["scene_descriptions"]),
            "design_text_available": bool(fields["design_text"])}
    }

def index_assets(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and deduplicate by URL; skip malformed records."""
    output, seen = [], set()
    for record in records:
        if not isinstance(record, Mapping): continue
        try: asset = normalize_asset(record)
        except ValueError: continue
        key = asset["url"].split("#", 1)[0].rstrip("/").casefold()
        if key not in seen: output.append(asset); seen.add(key)
    return output

def search_assets(query: str, assets: Iterable[Mapping[str, Any]], *,
                  media_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Search captions, transcripts, OCR, scene notes, tags and design text."""
    terms = _tokens(_text(query, 500))
    if not terms: return []
    selected = _text(media_type, 20).casefold() if media_type else None
    if selected is not None and selected not in MEDIA_TYPES:
        raise ValueError("media_type must be image, audio, video, or design")
    results = []
    for raw in assets:
        try: asset = normalize_asset(raw)
        except (ValueError, TypeError): continue
        if selected and asset["media_type"] != selected: continue
        fields = {
            "title": _tokens(asset["title"]), "tags": _tokens(" ".join(asset["tags"])),
            "description": _tokens(asset["description"]), "transcript": _tokens(asset["transcript"]),
            "scenes": _tokens(asset["scene_descriptions"]), "ocr": _tokens(asset["ocr_text"]),
            "design": _tokens(asset["design_text"]),
            "other": _tokens(" ".join((asset["alt_text"], asset["creator"], asset["collection"])))
        }
        if not terms.issubset(set().union(*fields.values())): continue
        score = sum(len(terms & fields[name]) * weight for name, weight in (
            ("title", 8), ("tags", 6), ("description", 4), ("transcript", 3),
            ("scenes", 3), ("ocr", 3), ("design", 3), ("other", 1)))
        results.append({**asset, "match_score": score})
    results.sort(key=lambda x: (-x["match_score"], x["url"]))
    return results[:max(1, min(int(limit), 100))]
