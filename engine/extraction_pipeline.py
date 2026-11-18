"""Validated adapter contract for optional multimodal extraction providers.

This module accepts structured outputs from OCR, transcription, video-scene and
design-text extractors. It does not invoke providers or execute extracted text.
Actual extraction must be gated by an explicit, recent source-terms attestation.
"""
from __future__ import annotations
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from media_intelligence import normalize_asset

EXTRACTION_TYPES = {"ocr", "transcript", "video_scenes", "design_text", "caption"}
MAX_RESULT_TEXT = 20000
MAX_SEGMENTS = 500
MAX_PROVIDER_TEXT = 120
MAX_TERMS_REVIEW_AGE_SECONDS = 90 * 24 * 60 * 60
FIELD_BY_TYPE = {
    "ocr": "ocr_text",
    "transcript": "transcript",
    "video_scenes": "scene_descriptions",
    "design_text": "design_text",
    "caption": "description",
}


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _url(value: Any) -> str:
    """Canonicalize only case-insensitive URL parts; preserve path and query case."""
    raw = _text(value, 2048)
    try:
        p = urlsplit(raw)
        if (
            p.scheme.casefold() not in {"http", "https"}
            or not p.hostname
            or p.username is not None
            or p.password is not None
        ):
            return ""
        _ = p.port
        return urlunsplit((p.scheme.casefold(), p.netloc.casefold(), p.path, p.query, ""))
    except ValueError:
        return ""


def _finite(value: Any, *, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _aware_timestamp(value: Any) -> datetime | None:
    raw = _text(value, 80)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_extraction_authorization(
    asset: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Require explicit robots permission and a recent source-terms review.

    This validates a caller-supplied policy attestation; it does not fetch or
    interpret source terms. A reviewer/approved policy process must establish
    that the cited terms permit extraction before setting these fields.
    """
    if not isinstance(asset, Mapping):
        raise ValueError("asset must be a mapping")
    if asset.get("robots_allowed") is not True:
        raise ValueError("extraction requires robots_allowed=true")
    if asset.get("extraction_permission") is not True:
        raise ValueError("extraction requires explicit extraction_permission=true")
    if asset.get("source_terms_status") != "allow_extraction":
        raise ValueError("source terms must explicitly allow extraction")

    terms_url = _url(asset.get("source_terms_url"))
    if not terms_url:
        raise ValueError("source_terms_url must be a public HTTP(S) URL")
    digest = _text(asset.get("source_terms_sha256"), 64).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("source_terms_sha256 must be a 64-character SHA-256 digest")

    checked_at = _aware_timestamp(asset.get("source_terms_checked_at"))
    if checked_at is None:
        raise ValueError("source_terms_checked_at must be a timezone-aware ISO timestamp")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    age = (current.astimezone(timezone.utc) - checked_at).total_seconds()
    if age < -300:
        raise ValueError("source terms review timestamp is in the future")
    if age > MAX_TERMS_REVIEW_AGE_SECONDS:
        raise ValueError("source terms review is older than 90 days")

    return {
        "allowed": True,
        "source_terms_url": terms_url,
        "source_terms_sha256": digest,
        "source_terms_checked_at": checked_at.isoformat(),
        "policy_basis": "explicit_source_terms_attestation_and_robots_permission",
    }


def normalize_extraction(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one provider result while preserving its provenance."""
    kind = _text(record.get("extraction_type"), 30).casefold()
    if kind not in EXTRACTION_TYPES:
        raise ValueError("unsupported extraction_type")
    source = _url(record.get("source_url"))
    if not source:
        raise ValueError("source_url must be a public HTTP(S) URL")
    provider = _text(record.get("provider"), MAX_PROVIDER_TEXT)
    model = _text(record.get("model"), MAX_PROVIDER_TEXT)
    if not provider or not model:
        raise ValueError("provider and model are required")
    raw_segments = record.get("segments") or []
    if not isinstance(raw_segments, (list, tuple)):
        raise ValueError("segments must be a list")
    if len(raw_segments) > MAX_SEGMENTS:
        raise ValueError("too many segments")
    segments = []
    for item in raw_segments:
        if not isinstance(item, Mapping):
            continue
        text = _text(item.get("text"), 2000)
        start, end = _finite(item.get("start_seconds")), _finite(item.get("end_seconds"))
        if not text or start is None or end is None or start < 0 or end < start or end > 86400:
            continue
        segments.append({"start_seconds": start, "end_seconds": end, "text": text})
    segments.sort(key=lambda item: (item["start_seconds"], item["end_seconds"]))
    text = _text(record.get("text"), MAX_RESULT_TEXT)
    if not text and segments:
        text = " ".join(item["text"] for item in segments)[:MAX_RESULT_TEXT]
    if not text:
        raise ValueError("extraction must contain text or valid segments")
    confidence = _finite(record.get("confidence"))
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))
    return {
        "source_url": source,
        "extraction_type": kind,
        "field": FIELD_BY_TYPE[kind],
        "text": text,
        "segments": segments,
        "provider": provider,
        "model": model,
        "language": _text(record.get("language"), 40),
        "confidence": confidence,
        "extracted_at": _text(record.get("extracted_at"), 80),
        "notes": _text(record.get("notes"), 500),
        "content_is_untrusted": True,
    }


def attach_extractions(
    asset: Mapping[str, Any], extractions: Iterable[Mapping[str, Any]] | None
) -> dict[str, Any]:
    """Attach matching text only when explicit source extraction permission exists."""
    rows = list(extractions or [])
    if len(rows) > MAX_SEGMENTS:
        raise ValueError("too many extractions for one asset")
    if rows:
        validate_extraction_authorization(asset)

    normalized = normalize_asset(asset)
    asset_url = _url(normalized["url"])
    merged = dict(normalized)
    provenance = list(merged.get("extraction_provenance") or [])
    grouped: dict[str, list[str]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        item = normalize_extraction(raw)
        if item["source_url"] != asset_url:
            continue
        grouped.setdefault(item["field"], []).append(item["text"])
        provenance.append({key: item[key] for key in (
            "extraction_type", "provider", "model", "language",
            "confidence", "extracted_at", "source_url")})
    for field, values in grouped.items():
        existing = _text(merged.get(field), MAX_RESULT_TEXT)
        merged[field] = _text(" ".join([existing, *values]), MAX_RESULT_TEXT)
    result = normalize_asset(merged)
    result["extraction_provenance"] = provenance[-MAX_SEGMENTS:]
    return result
