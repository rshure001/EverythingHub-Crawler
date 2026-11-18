"""Auditable, bounded intelligence layer for Everything Hub.

The layer scores evidence, connects corroborating documents, and learns only from
explicit feedback. It is deterministic, dependency-free, and has no network,
filesystem, crawler, publishing, or database side effects.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import log1p
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

FEATURES = ("relevance", "source_quality", "freshness", "corroboration")
DEFAULT_WEIGHTS = {"relevance": 0.45, "source_quality": 0.25,
                   "freshness": 0.15, "corroboration": 0.15}
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.75
MAX_FEEDBACK_STEP = 0.025
MAX_FEEDBACK_EVENTS = 10000
FEEDBACK_LABELS = {"helpful", "not_helpful", "verified", "disputed"}


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(1.0, number))


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _freshness(value: Any, now: datetime) -> float:
    stamp = _timestamp(value)
    if stamp is None:
        return 0.35  # unknown age is not treated as fresh
    age_days = max(0.0, (now - stamp).total_seconds() / 86400)
    return max(0.05, min(1.0, 2 ** (-age_days / 180)))


def _normalise_weights(weights: Mapping[str, Any] | None) -> dict[str, float]:
    raw = {key: _unit((weights or {}).get(key, default), default)
           for key, default in DEFAULT_WEIGHTS.items()}
    bounded = {key: max(MIN_WEIGHT, min(MAX_WEIGHT, value))
               for key, value in raw.items()}
    total = sum(bounded.values())
    return {key: value / total for key, value in bounded.items()}


def _corroboration(page: Mapping[str, Any], corpus: list[Mapping[str, Any]]) -> float:
    target = str(page.get("url") or "")
    target_host = _host(target)
    if not target_host:
        return 0.0
    topic = str(page.get("topic_key") or "").strip().casefold()
    if not topic:
        return 0.0
    hosts = {
        _host(item.get("url", ""))
        for item in corpus
        if isinstance(item, Mapping)
        and str(item.get("topic_key") or "").strip().casefold() == topic
        and item.get("robots_allowed") is not False
        and _host(str(item.get("url") or ""))
        and _host(str(item.get("url") or "")) != target_host
    }
    # Saturating reward: independent hosts add evidence, not certainty.
    return min(1.0, log1p(len(hosts)) / log1p(5))


def score_evidence(
    page: Mapping[str, Any],
    corpus: Iterable[Mapping[str, Any]] = (),
    *,
    weights: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a transparent 0..1 evidence score and its component breakdown.

    Inputs are metadata supplied by the caller; source_quality is not inferred
    from a domain name. Robots-denied and invalid-URL records are excluded.
    """
    url = str(page.get("url") or "").strip()
    if not url.startswith(("https://", "http://")) or page.get("robots_allowed") is False:
        return {"eligible": False, "score": 0.0, "components": {},
                "reason": "invalid_url_or_robots_denied"}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    pages = [item for item in corpus if isinstance(item, Mapping)]
    components = {
        "relevance": _unit(page.get("relevance_score")),
        "source_quality": _unit(page.get("source_quality"), 0.5),
        "freshness": _freshness(page.get("fetched_at"), current),
        "corroboration": _corroboration(page, pages),
    }
    selected_weights = _normalise_weights(weights)
    score = sum(components[key] * selected_weights[key] for key in FEATURES)
    return {
        "eligible": True,
        "score": round(max(0.0, min(1.0, score)), 6),
        "confidence_band": ("high" if score >= 0.75 else
                            "medium" if score >= 0.45 else "low"),
        "components": {key: round(value, 6) for key, value in components.items()},
        "weights": {key: round(value, 6) for key, value in selected_weights.items()},
        "corroborating_hosts": len({
            _host(item.get("url", "")) for item in pages
            if isinstance(item, Mapping)
            and str(item.get("topic_key") or "").strip().casefold()
                == str(page.get("topic_key") or "").strip().casefold()
            and page.get("topic_key")
            and _host(str(item.get("url") or ""))
            and _host(str(item.get("url") or "")) != _host(url)
            and item.get("robots_allowed") is not False
        }),
        "reason": "scored_from_explicit_metadata",
    }


def rank_evidence(
    pages: Iterable[Mapping[str, Any]],
    *,
    weights: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Score and deterministically rank eligible evidence records."""
    corpus = [page for page in pages if isinstance(page, Mapping)]
    safe_limit = max(1, min(int(limit), 50))
    results = []
    seen = set()
    for page in corpus:
        url = str(page.get("url") or "").strip()
        if url in seen:
            continue
        seen.add(url)
        evidence = score_evidence(page, corpus, weights=weights, now=now)
        if not evidence["eligible"]:
            continue
        results.append({
            "url": url,
            "title": str(page.get("title") or url)[:500],
            "topic_key": str(page.get("topic_key") or "")[:160],
            **evidence,
        })
    results.sort(key=lambda item: (-item["score"], item["url"]))
    return results[:safe_limit]


def learn_weights(
    events: Iterable[Mapping[str, Any]],
    *,
    prior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Learn bounded feature weights from explicit, auditable feedback events.

    Each event must carry a human feedback label and a feature_snapshot in
    [0,1]. Feedback never changes source metadata or crawler policy. A bounded
    online update moves weights toward features associated with positive
    outcomes and away from features associated with negative outcomes.
    """
    weights = _normalise_weights(prior)
    used = 0
    ignored = 0
    for event in events:
        if used >= MAX_FEEDBACK_EVENTS:
            ignored += 1
            continue
        if not isinstance(event, Mapping) or event.get("label") not in FEEDBACK_LABELS:
            ignored += 1
            continue
        snapshot = event.get("feature_snapshot")
        if not isinstance(snapshot, Mapping) or any(key not in snapshot for key in FEATURES):
            ignored += 1
            continue
        features = {key: _unit(snapshot[key], -1.0) for key in FEATURES}
        if any(value < 0 for value in features.values()):
            ignored += 1
            continue
        label = event["label"]
        direction = 1.0 if label in {"helpful", "verified"} else -1.0
        # Center features so feedback reinforces discriminating signals rather
        # than uniformly inflating every coefficient.
        mean = sum(features.values()) / len(FEATURES)
        step = MAX_FEEDBACK_STEP * direction
        updated = {
            key: max(MIN_WEIGHT, min(MAX_WEIGHT,
                    weights[key] + step * (features[key] - mean)))
            for key in FEATURES
        }
        weights = _normalise_weights(updated)
        used += 1
    return {
        "weights": {key: round(value, 6) for key, value in weights.items()},
        "feedback_used": used,
        "feedback_ignored": ignored,
        "learning_policy": "explicit_feedback_bounded_v1",
    }
