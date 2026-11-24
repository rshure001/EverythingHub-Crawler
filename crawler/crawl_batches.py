#!/usr/bin/env python3
"""Run-scoped immutable crawl batches and deterministic single-writer reduction.

Workers write one batch each. Only the reducer merges batches into the canonical
index; this module intentionally does not mutate the shared index or frontier.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _valid_page_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.hostname.lower() not in {"localhost"}
        and not parsed.hostname.lower().endswith((".localhost", ".local", ".internal"))
    )


def validate_batch(batch: Any) -> dict[str, Any]:
    """Validate a complete worker batch; reject malformed input as a unit."""
    if not isinstance(batch, dict) or batch.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported crawl batch schema_version")
    for field in ("run_id", "worker_id"):
        value = batch.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"crawl batch {field} must be a non-empty string")
    pages = batch.get("pages")
    if not isinstance(pages, list):
        raise ValueError("crawl batch pages must be a list")
    checked = []
    for position, page in enumerate(pages):
        if not isinstance(page, dict) or not _valid_page_url(page.get("url")):
            raise ValueError(f"crawl batch page {position} has an invalid HTTPS URL")
        try:
            fetched_at = float(page.get("fetched_at", 0))
        except (TypeError, ValueError):
            raise ValueError(f"crawl batch page {position} has an invalid fetched_at")
        if not math.isfinite(fetched_at) or fetched_at < 0:
            raise ValueError(f"crawl batch page {position} has an invalid fetched_at")
        # Ensure every field is JSON-safe and deterministic before accepting it.
        _canonical_json(page)
        checked.append(dict(page))
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": batch["run_id"],
        "worker_id": batch["worker_id"],
        "pages": checked,
    }


def write_batch(path: str | Path, *, run_id: str, worker_id: str,
                pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Atomically write one validated, immutable worker batch."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    batch = validate_batch({
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "worker_id": worker_id,
        "pages": pages,
    })
    raw = _canonical_json(batch) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp",
                                     dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {
        "path": str(target),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "page_count": len(batch["pages"]),
    }


def read_batch(path: str | Path) -> dict[str, Any]:
    """Read and validate one persisted batch."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read crawl batch {path}: {exc}") from exc
    return validate_batch(payload)


def _winner_key(page: dict[str, Any]) -> tuple[float, bytes]:
    """Newer fetch wins; equal timestamps use canonical bytes as a stable tie-break."""
    return float(page.get("fetched_at", 0) or 0), _canonical_json(page)


def reduce_page_batches(existing_pages: list[dict[str, Any]],
                        batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge validated batches independent of input order; safe to replay.

    For each exact canonical URL, keep the newest fetched_at record. Ties use
    lexicographically greatest canonical JSON bytes, making results independent
    of worker completion order and batch enumeration order.
    """
    if not isinstance(existing_pages, list):
        raise ValueError("existing_pages must be a list")
    winners: dict[str, dict[str, Any]] = {}
    for page in existing_pages:
        if not isinstance(page, dict) or not _valid_page_url(page.get("url")):
            raise ValueError("existing canonical index contains an invalid page URL")
        _canonical_json(page)
        current = winners.get(page["url"])
        if current is None or _winner_key(page) > _winner_key(current):
            winners[page["url"]] = dict(page)

    duplicate_occurrences = 0
    replaced_existing = 0
    accepted_batch_pages = 0
    run_ids: set[str] = set()
    worker_ids: set[str] = set()
    for raw_batch in batches:
        batch = validate_batch(raw_batch)
        run_ids.add(batch["run_id"])
        worker_ids.add(batch["worker_id"])
        for page in batch["pages"]:
            accepted_batch_pages += 1
            url = page["url"]
            current = winners.get(url)
            if current is not None:
                duplicate_occurrences += 1
                if _winner_key(page) > _winner_key(current):
                    if any(existing.get("url") == url for existing in existing_pages):
                        replaced_existing += 1
                    winners[url] = page
            else:
                winners[url] = page

    ordered = [winners[url] for url in sorted(winners)]
    return {
        "schema_version": SCHEMA_VERSION,
        "pages": ordered,
        "metrics": {
            "existing_pages": len(existing_pages),
            "batch_count": len(batches),
            "worker_count": len(worker_ids),
            "run_ids": sorted(run_ids),
            "batch_pages": accepted_batch_pages,
            "duplicate_occurrences": duplicate_occurrences,
            "replaced_existing": replaced_existing,
            "unique_pages": len(ordered),
        },
    }


def load_and_reduce(existing_pages: list[dict[str, Any]],
                    batch_paths: list[str | Path]) -> dict[str, Any]:
    """Load batches in path order, validate them, and run deterministic reduction."""
    batches = [read_batch(path) for path in sorted(map(Path, batch_paths), key=str)]
    return reduce_page_batches(existing_pages, batches)
