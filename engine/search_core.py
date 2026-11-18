"""Dependency-free search core for Everything Hub's static crawler index.

This module ranks already-published page records. It performs no network access,
does not mutate the crawler frontier, and never bypasses source access policies.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Mapping, Any

from intelligence_core import score_evidence

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
FIELD_WEIGHTS = {"title": 4.0, "url": 2.2, "description": 1.7, "text": 1.0}
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_QUERY_CHARS = 240


def tokenize(value: Any) -> list[str]:
    """Unicode-normalize and tokenize text consistently across fields."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return TOKEN_RE.findall(normalized)


def _field_tokens(page: Mapping[str, Any]) -> dict[str, list[str]]:
    return {name: tokenize(page.get(name, "")) for name in FIELD_WEIGHTS}


def _snippet(page: Mapping[str, Any], terms: set[str], max_chars: int = 220) -> str:
    description = str(page.get("description") or "").strip()
    body = description or str(page.get("text") or "").strip()
    if not body:
        return ""
    words = body.split()
    for index, word in enumerate(words):
        if terms.intersection(tokenize(word)):
            start = max(0, index - 9)
            excerpt = " ".join(words[start:index + 24])
            return ("…" if start else "") + excerpt[:max_chars]
    return body[:max_chars]


def search(
    pages: Iterable[Mapping[str, Any]],
    query: str,
    *,
    lane: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return deterministic, BM25-ranked results with title/URL field boosts."""
    raw_query = str(query or "").strip()
    if len(raw_query) > MAX_QUERY_CHARS:
        raw_query = raw_query[:MAX_QUERY_CHARS]
    terms = tokenize(raw_query)
    if not terms:
        return []

    safe_limit = max(1, min(int(limit), MAX_LIMIT))
    phrase = " ".join(terms)
    candidates = []
    seen_urls = set()

    for page in pages:
        if not isinstance(page, Mapping):
            continue
        url = str(page.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        if lane and str(page.get("lane") or "").casefold() != lane.casefold():
            continue
        seen_urls.add(url)
        fields = _field_tokens(page)
        corpus = [token for values in fields.values() for token in values]
        if not corpus:
            continue
        counts = Counter(corpus)
        score = 0.0
        for term in set(terms):
            tf = counts[term]
            if not tf:
                continue
            # Saturating term-frequency contribution; field boosts reward
            # matches in titles and URLs without requiring exact title matches.
            weighted_tf = sum(
                FIELD_WEIGHTS[field] * Counter(values)[term]
                for field, values in fields.items()
            )
            score += (weighted_tf / (weighted_tf + 1.2)) * (1.0 + math.log1p(tf))
        title = " ".join(fields["title"])
        url_text = " ".join(fields["url"])
        if phrase and phrase in title:
            score += 5.0
        if phrase and phrase in url_text:
            score += 2.0
        if score <= 0:
            continue
        candidates.append({
            "url": url,
            "title": str(page.get("title") or url)[:500],
            "description": str(page.get("description") or "")[:500],
            "lane": str(page.get("lane") or ""),
            "snippet": _snippet(page, set(terms)),
            "score": round(score, 6),
            "fetched_at": page.get("fetched_at"),
        })

    if candidates:
        max_lexical_score = max(item["score"] for item in candidates) or 1.0
        evidence_pages = [
            {
                "url": item["url"],
                "topic_key": phrase,
                "relevance_score": item["score"] / max_lexical_score,
                "fetched_at": item.get("fetched_at"),
                "robots_allowed": True,
            }
            for item in candidates
        ]
        for item, evidence_page in zip(candidates, evidence_pages):
            evidence = score_evidence(evidence_page, evidence_pages)
            item["intelligence_score"] = evidence["score"]
            item["evidence_band"] = evidence["evidence_band"]
            item["evidence_components"] = evidence["components"]
            item["corroborating_hosts"] = evidence["corroborating_hosts"]

    candidates.sort(key=lambda item: (
        -item.get("intelligence_score", item["score"]),
        -item["score"],
        item["title"].casefold(),
        item["url"],
    ))
    return candidates[:safe_limit]
