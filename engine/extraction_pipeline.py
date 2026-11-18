"""Validated adapter contract for optional multimodal extraction providers.

This module accepts structured outputs from OCR, transcription, video-scene and
design-text extractors. It does not invoke providers or execute extracted text.
"""
from __future__ import annotations
import math
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from media_intelligence import normalize_asset

EXTRACTION_TYPES = {"ocr", "transcript", "video_scenes", "design_text", "caption"}
MAX_RESULT_TEXT = 20000
MAX_SEGMENTS = 500
MAX_PROVIDER_TEXT = 120
FIELD_BY_TYPE = {
    "ocr": "ocr_text",
    "transcript": "transcript",
    "video_scenes": "scene_descriptions",
    "design_text": "design_text",
    "caption": "description",
}

def _text(value: Any, limit: int) -> str:
    if value is None: return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]

def _url(value: Any) -> str:
    raw = _text(value, 2048)
    try:
        p = urlsplit(raw)
        if p.scheme.casefold() not in {"http", "https"} or not p.hostname or p.username or p.password: return ""
        return raw.split("#", 1)[0].rstrip("/").casefold()
    except ValueError: return ""

def _finite(value: Any, *, default: float | None = None) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): return default
    return number if math.isfinite(number) else default

def normalize_extraction(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one provider result while preserving its provenance."""
    kind = _text(record.get("extraction_type"), 30).casefold()
    if kind not in EXTRACTION_TYPES: raise ValueError("unsupported extraction_type")
    source = _url(record.get("source_url"))
    if not source: raise ValueError("source_url must be a public HTTP(S) URL")
    provider = _text(record.get("provider"), MAX_PROVIDER_TEXT)
    model = _text(record.get("model"), MAX_PROVIDER_TEXT)
    if not provider or not model: raise ValueError("provider and model are required")
    raw_segments = record.get("segments") or []
    if not isinstance(raw_segments, (list, tuple)): raise ValueError("segments must be a list")
    if len(raw_segments) > MAX_SEGMENTS: raise ValueError("too many segments")
    segments = []
    for item in raw_segments:
        if not isinstance(item, Mapping): continue
        text = _text(item.get("text"), 2000)
        start, end = _finite(item.get("start_seconds")), _finite(item.get("end_seconds"))
        if not text or start is None or end is None or start < 0 or end < start or end > 86400:
            continue
        segments.append({"start_seconds": start, "end_seconds": end, "text": text})
    segments.sort(key=lambda item: (item["start_seconds"], item["end_seconds"]))
    text = _text(record.get("text"), MAX_RESULT_TEXT)
    if not text and segments:
        text = " ".join(item["text"] for item in segments)[:MAX_RESULT_TEXT]
    if not text: raise ValueError("extraction must contain text or valid segments")
    confidence = _finite(record.get("confidence"))
    if confidence is not None: confidence = max(0.0, min(1.0, confidence))
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

def attach_extractions(asset: Mapping[str, Any],
                       extractions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Attach matching extractor text to an asset without changing its rights."""
    normalized = normalize_asset(asset)
    asset_url = _url(normalized["url"])
    merged = dict(normalized)
    provenance = list(merged.get("extraction_provenance") or [])
    grouped: dict[str, list[str]] = {}
    for raw in extractions:
        if not isinstance(raw, Mapping): continue
        item = normalize_extraction(raw)
        if item["source_url"] != asset_url: continue
        grouped.setdefault(item["field"], []).append(item["text"])
        provenance.append({key: item[key] for key in (
            "extraction_type", "provider", "model", "language",
            "confidence", "extracted_at", "source_url")})
    for field, values in grouped.items():
        existing = _text(merged.get(field), MAX_RESULT_TEXT)
        merged[field] = _text(" ".join([existing, *values]), MAX_RESULT_TEXT)
    merged["extraction_provenance"] = provenance[-MAX_SEGMENTS:]
    return normalize_asset(merged)
