#!/usr/bin/env python3
"""Bounded expansion of evidence-backed catalog landing pages.

Consumes the source-review queue built from the validated page-derived catalog
registry. It never treats every source page as a catalog. Each run may inspect
at most ten policy-preapproved catalog landing pages, rechecks robots.txt and
source terms at request time, and extracts only same-origin article-link
metadata. It never fetches child pages, full text, media, or bulk assets.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from catalog_source_policy import (
    SourcePolicyError,
    _purpose_path_allowed,
    policy_for_host,
    request_source_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "data" / "page-derived-catalogs" / "source-host-review-queue.json"
INDEX = ROOT / "public-search" / "manifest.json"
STATE = ROOT / "data" / "page-catalog-expansion.json.gz"
MAX_BATCH = 10
MAX_CHILDREN_PER_PAGE = 200
MAX_HTML_BYTES = 1_500_000
MAX_QUEUE_AGE_SECONDS = 6 * 60 * 60
MAX_QUEUE_PAGE_LAG = 10_000
RETRYABLE_ERROR_COOLDOWN_SECONDS = 2 * 60 * 60
HTTP_ERROR_COOLDOWN_SECONDS = 6 * 60 * 60
POLICY_BLOCK_COOLDOWN_SECONDS = 24 * 60 * 60
REVIEWED_STATUSES = {"reviewed", "reviewed_per_dataset"}


def _read_payload(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _write_payload(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    if path.suffix == ".gz":
        temp.write_bytes(gzip.compress(raw, mtime=0))
    else:
        temp.write_bytes(raw)
    temp.replace(path)


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _current_index_snapshot(index_path: Path = INDEX) -> dict | None:
    try:
        raw = index_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    pages = manifest.get("total_pages")
    updated_at = manifest.get("updated_at")
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 0:
        return None
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)) or updated_at <= 0:
        return None
    try:
        updated = datetime.fromtimestamp(updated_at, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return {"pages": pages, "sha256": hashlib.sha256(raw).hexdigest(), "updated_at": updated}


def _queue_snapshot_status(queue: object, current: dict | None) -> dict:
    status = {
        "compatible": False,
        "exact_match": False,
        "page_lag": None,
        "age_seconds": None,
    }
    if not isinstance(queue, dict) or current is None:
        return status
    if queue.get("status") != "source_review_backlog_not_a_fetch_queue":
        return status
    source_pages = queue.get("source_index_pages")
    source_sha = queue.get("source_index_sha256")
    source_updated = _parse_utc_timestamp(queue.get("source_index_updated_at"))
    if isinstance(source_pages, bool) or not isinstance(source_pages, int) or source_pages < 0:
        return status
    if (
        not isinstance(source_sha, str)
        or len(source_sha) != 64
        or any(char not in "0123456789abcdef" for char in source_sha.lower())
        or source_updated is None
    ):
        return status
    page_lag = current["pages"] - source_pages
    age_seconds = (current["updated_at"] - source_updated).total_seconds()
    exact_match = page_lag == 0 and source_sha == current["sha256"]
    compatible = (
        0 <= page_lag <= MAX_QUEUE_PAGE_LAG
        and 0 <= age_seconds <= MAX_QUEUE_AGE_SECONDS
        # A same-size manifest with a different hash is not an append-only
        # advance; fail closed rather than treating changed/replaced pages as
        # compatible with an older review queue.
        and (page_lag > 0 or source_sha == current["sha256"])
    )
    status.update({
        "compatible": compatible,
        "exact_match": exact_match,
        "page_lag": page_lag,
        "age_seconds": age_seconds,
    })
    return status


def _queue_snapshot_compatible_with_current_index(queue: object, index_path: Path = INDEX) -> bool:
    return _queue_snapshot_status(queue, _current_index_snapshot(index_path))["compatible"]


def _queue_matches_current_index(queue: object, index_path: Path = INDEX) -> bool:
    return _queue_snapshot_status(queue, _current_index_snapshot(index_path))["exact_match"]


def _eligible_seed(seed: object) -> bool:
    """Accept only actual catalog-definition URLs from a current review queue."""
    if not isinstance(seed, dict):
        return False
    if seed.get("review_status") != "policy_preapproved_robots_recheck_required":
        return False
    if seed.get("policy_preapproved") is not True:
        return False
    if seed.get("source_access_mode") != "catalog_landing_page":
        return False
    if seed.get("metadata_only") is not True or seed.get("robots_recheck_required") is not True:
        return False
    if seed.get("fetch_authorized") is not False:
        return False
    catalog_ids = seed.get("catalog_ids")
    if (
        not isinstance(catalog_ids, list)
        or not catalog_ids
        or any(not isinstance(value, str) or not value for value in catalog_ids)
        or len(catalog_ids) != len(set(catalog_ids))
    ):
        return False
    if seed.get("terms_status") not in REVIEWED_STATUSES or not seed.get("terms_url"):
        return False

    url = seed.get("source_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password:
        return False

    fields = seed.get("allowed_metadata_fields")
    prohibited = seed.get("prohibited_content")
    if not isinstance(fields, list) or "article_url" not in fields or "title" not in fields:
        return False
    if not isinstance(prohibited, list) or not prohibited:
        return False

    policy = policy_for_host(parsed.hostname)
    if not policy or policy.get("discovery_allowed") is not True:
        return False
    if policy.get("terms_status") not in REVIEWED_STATUSES or not policy.get("terms_url"):
        return False
    if policy.get("metadata_only") is not True:
        return False
    policy_fields = policy.get("allowed_metadata_fields")
    policy_prohibited = policy.get("prohibited_content")
    if not isinstance(policy_fields, list) or "article_url" not in policy_fields or "title" not in policy_fields:
        return False
    if not isinstance(policy_prohibited, list) or not policy_prohibited:
        return False
    return _purpose_path_allowed(parsed.path, policy, "landing_page")


def _canonical_fetch_url(url: str, policy: dict) -> str:
    """Normalize only to the separately reviewed canonical host for this source."""
    parsed = urlsplit(url)
    canonical_host = policy.get("canonical_fetch_host") or parsed.hostname
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourcePolicyError("invalid catalog landing-page URL port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not isinstance(canonical_host, str)
        or not canonical_host
    ):
        raise SourcePolicyError("catalog canonical-host configuration or source URL is invalid")
    source_policy = policy_for_host(parsed.hostname)
    canonical_policy = policy_for_host(canonical_host)
    if (
        not source_policy
        or not canonical_policy
        or canonical_policy.get("terms_status") not in REVIEWED_STATUSES
        or canonical_policy.get("discovery_allowed") is not True
        or canonical_policy.get("metadata_only") is not True
        or canonical_policy.get("canonical_fetch_host") not in (None, canonical_host)
        or not _purpose_path_allowed(parsed.path, canonical_policy, "landing_page")
    ):
        raise SourcePolicyError("canonical fetch host is not independently reviewed for this catalog path")
    return urlunsplit(("https", canonical_host, parsed.path, parsed.query, ""))


def _source_key(seed: dict) -> str:
    url = str(seed.get("source_url") or "")
    return "catalog-source:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _should_attempt_seed(prior: object, now: datetime | None = None) -> bool:
    if not isinstance(prior, dict):
        return True
    status = prior.get("status")
    if status == "metadata_listing_processed":
        return False
    cooldowns = {
        "retryable_error": RETRYABLE_ERROR_COOLDOWN_SECONDS,
        "http_error": HTTP_ERROR_COOLDOWN_SECONDS,
        "policy_or_robots_blocked": POLICY_BLOCK_COOLDOWN_SECONDS,
    }
    cooldown = cooldowns.get(status)
    if cooldown is None:
        return True
    checked_at = _parse_utc_timestamp(prior.get("checked_at"))
    if checked_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - checked_at).total_seconds() >= cooldown


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "a":
            href = values.get("href")
            self._anchor_href = href if isinstance(href, str) else None
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() == "a" and self._anchor_href:
            label = " ".join("".join(self._anchor_parts).split())[:300]
            self.links.append((self._anchor_href, label))
            self._anchor_href = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)


def _canonical_child(parent_url: str, href: str) -> str | None:
    try:
        candidate = urljoin(parent_url, href)
        parsed = urlsplit(candidate)
        parent = urlsplit(parent_url)
        parsed_port = parsed.port or 443
        parent_port = parent.port or 443
    except ValueError:
        return None
    # Require the exact HTTPS origin, not merely the same hostname.
    # Non-default ports are intentionally excluded from metadata discovery.
    if (
        parsed.scheme != "https"
        or parent.scheme != "https"
        or parsed_port != 443
        or parent_port != 443
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname.lower().rstrip(".") != (parent.hostname or "").lower().rstrip(".")
    ):
        return None
    if parsed.path == parent.path or not parsed.path.startswith(parent.path.rstrip("/") + "/"):
        return None
    if "/articles/" not in parsed.path and not parsed.path.rstrip("/").endswith("/article"):
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def extract_child_records(
    html: str,
    source_url: str,
    catalog_ids: list[str],
    source_metadata: dict | None = None,
    observed_at: str | None = None,
) -> list[dict]:
    normalized_catalog_ids = sorted({value for value in catalog_ids if isinstance(value, str) and value})
    if not normalized_catalog_ids:
        return []
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    parser = _ListingParser()
    parser.feed(html)
    records = []
    seen = set()
    source_host = urlsplit(source_url).hostname
    provenance = {
        "source_url": source_url,
        "catalog_ids": normalized_catalog_ids,
        "terms_url": source_metadata.get("terms_url"),
        "terms_status": source_metadata.get("terms_status"),
        "attribution_required": source_metadata.get("attribution_required"),
        "rights_scope": source_metadata.get("rights_scope"),
        "discovered_at": observed_at,
    }
    for href, label in parser.links:
        url = _canonical_child(source_url, href)
        if not url or not label or url in seen:
            continue
        seen.add(url)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        records.append(
            {
                "record_id": "page-child:" + digest,
                "catalog_ids": normalized_catalog_ids,
                "title": label,
                "article_url": url,
                "source_url": source_url,
                "source_urls": [source_url],
                "source_host": source_host,
                "source_hosts": [source_host] if source_host else [],
                "source_provenance": [provenance],
                "record_type": "article_link_metadata",
                "rights_status": "per_record_review_required",
                "source_terms_url": source_metadata.get("terms_url"),
                "source_terms_status": source_metadata.get("terms_status"),
                "attribution_required": source_metadata.get("attribution_required"),
                "verified": False,
                "content_fetched": False,
                "discovered_at": observed_at,
                "last_seen_at": observed_at,
            }
        )
        if len(records) >= MAX_CHILDREN_PER_PAGE:
            break
    return records


def _merge_record(existing: dict, incoming: dict) -> None:
    for field in ("catalog_ids", "source_urls", "source_hosts"):
        existing_values = existing.get(field) if isinstance(existing.get(field), list) else []
        incoming_values = incoming.get(field) if isinstance(incoming.get(field), list) else []
        existing[field] = sorted({value for value in existing_values + incoming_values if isinstance(value, str) and value})
    provenance = existing.get("source_provenance")
    if not isinstance(provenance, list):
        provenance = []
    seen = {
        (row.get("source_url"), tuple(row.get("catalog_ids", [])))
        for row in provenance
        if isinstance(row, dict)
    }
    for row in incoming.get("source_provenance", []):
        if not isinstance(row, dict):
            continue
        key = (row.get("source_url"), tuple(row.get("catalog_ids", [])))
        if key not in seen:
            provenance.append(row)
            seen.add(key)
    existing["source_provenance"] = provenance
    existing["last_seen_at"] = incoming.get("last_seen_at") or existing.get("last_seen_at")


def run_batch(
    queue_path: Path = QUEUE,
    state_path: Path = STATE,
    limit: int = MAX_BATCH,
    index_path: Path = INDEX,
) -> dict:
    if not 1 <= limit <= MAX_BATCH:
        raise ValueError(f"batch limit must be between 1 and {MAX_BATCH}")

    queue = _read_payload(queue_path)
    state = _read_payload(state_path)
    prior_records = state.get("records", [])
    if not isinstance(prior_records, list):
        prior_records = []
    records_by_id = {
        row["record_id"]: row
        for row in prior_records
        if isinstance(row, dict) and isinstance(row.get("record_id"), str)
    }
    outcomes = state.get("source_outcomes", {})
    if not isinstance(outcomes, dict):
        outcomes = {}

    current_index = _current_index_snapshot(index_path)
    queue_status = _queue_snapshot_status(queue, current_index)
    queue_is_compatible = queue_status["compatible"]
    queue_pages = queue.get("source_index_pages") if isinstance(queue, dict) else None
    queue_sha = queue.get("source_index_sha256") if isinstance(queue, dict) else None
    queue_updated_at = queue.get("source_index_updated_at") if isinstance(queue, dict) else None
    if not queue_is_compatible:
        result = {
            "schema_version": 2,
            "status": "waiting_for_fresh_source_review_queue",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "queue_matches_current_index": queue_status["exact_match"],
            "queue_snapshot_compatible_with_current_index": False,
            "queue_source_index_sha_matches_current": queue_status["exact_match"],
            "queue_source_index_page_lag": queue_status["page_lag"],
            "queue_source_index_age_seconds": queue_status["age_seconds"],
            "queue_source_index_pages": queue_pages,
            "queue_source_index_sha256": queue_sha,
            "queue_source_index_updated_at": queue_updated_at,
            "source_index_pages": current_index["pages"] if current_index else None,
            "source_index_sha256": current_index["sha256"] if current_index else None,
            "eligible_catalog_landing_pages": 0,
            "last_batch_size": 0,
            "last_batch_processed": 0,
            "last_batch_new_unique_child_records": 0,
            "next_source_offset": 0,
            "source_outcomes": outcomes,
            "records": list(records_by_id.values()),
            "metrics": {
                "unique_child_records_staged": len(records_by_id),
                "catalog_memberships": sum(len(row.get("catalog_ids", [])) for row in records_by_id.values()),
                "catalog_landing_pages_processed": sum(
                    1 for value in outcomes.values()
                    if isinstance(value, dict) and value.get("status") == "metadata_listing_processed"
                ),
                "verified_catalogs": 0,
                "records_published": 0,
                "child_pages_fetched": 0,
                "full_text_fetched": 0,
                "media_fetched": 0,
            },
        }
        # A stale review queue is not a progress event. Preserve the previous
        # resumable ledger (or leave it absent) so the scheduled workflow does
        # not create a timestamp-only commit while waiting for a fresh queue.
        return result

    candidate_urls = queue.get("candidate_urls", [])
    if not isinstance(candidate_urls, list):
        candidate_urls = []
    eligible = [seed for seed in candidate_urls if _eligible_seed(seed)]

    cursor = int(state.get("next_source_offset", 0) or 0)
    if eligible:
        cursor %= len(eligible)
        now = datetime.now(timezone.utc)
        pending_by_host: dict[str, list[tuple[int, dict]]] = {}
        for offset in range(len(eligible)):
            seed = eligible[(cursor + offset) % len(eligible)]
            source_id = _source_key(seed)
            if not _should_attempt_seed(outcomes.get(source_id), now):
                continue
            host = (urlsplit(seed["source_url"]).hostname or "").lower()
            pending_by_host.setdefault(host, []).append((offset, seed))

        # Round-robin across approved source hosts so one large publisher cannot
        # monopolize every bounded batch when other reviewed hosts are available.
        batch = []
        selected_offsets = []
        while pending_by_host and len(batch) < limit:
            for host in list(pending_by_host):
                rows = pending_by_host[host]
                if rows:
                    offset, seed = rows.pop(0)
                    batch.append(seed)
                    selected_offsets.append(offset)
                if not rows:
                    del pending_by_host[host]
                if len(batch) >= limit:
                    break
        next_cursor = (cursor + max(selected_offsets) + 1) % len(eligible) if selected_offsets else cursor
    else:
        batch, next_cursor = [], 0

    processed = 0
    discovered = 0
    for seed in batch:
        source_id = _source_key(seed)
        source_url = seed["source_url"]
        catalog_ids = seed["catalog_ids"]
        try:
            source_policy = policy_for_host(urlsplit(source_url).hostname or "")
            if not source_policy:
                raise SourcePolicyError("catalog source policy is unavailable")
            url = _canonical_fetch_url(source_url, source_policy)
            raw, status, headers, policy = request_source_bytes(
                url,
                purpose="landing_page",
                max_bytes=MAX_HTML_BYTES,
                timeout=10,
                accept="text/html",
            )
            if status != 200:
                outcomes[source_id] = {
                    "status": "http_error",
                    "http_status": status,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "catalog_ids": catalog_ids,
                }
                continue
            charset = "utf-8"
            content_type = headers.get("Content-Type", "")
            if "charset=" in content_type.lower():
                charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
            html = raw.decode(charset, errors="replace")
            checked_at = datetime.now(timezone.utc).isoformat()
            children = extract_child_records(html, url, catalog_ids, {**seed, "source_url": url}, checked_at)
            before = len(records_by_id)
            for record in children:
                record_id = record["record_id"]
                if record_id in records_by_id:
                    _merge_record(records_by_id[record_id], record)
                else:
                    records_by_id[record_id] = record
            added = len(records_by_id) - before
            discovered += added
            processed += 1
            outcomes[source_id] = {
                "status": "metadata_listing_processed",
                "checked_at": checked_at,
                "catalog_ids": catalog_ids,
                "child_link_candidates": len(children),
                "new_unique_child_records": added,
                "terms_url": policy.get("terms_url"),
                "terms_status": policy.get("terms_status"),
            }
        except SourcePolicyError as exc:
            outcomes[source_id] = {
                "status": "policy_or_robots_blocked",
                "reason": str(exc)[:240],
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "catalog_ids": catalog_ids,
            }
        except (OSError, UnicodeError, ValueError, TimeoutError) as exc:
            response = getattr(exc, "response", None)
            http_status = getattr(response, "status_code", None) or getattr(exc, "code", None)
            terminal_http_statuses = {403, 404, 410, 451}
            outcomes[source_id] = {
                "status": "http_error" if http_status in terminal_http_statuses else "retryable_error",
                "reason": type(exc).__name__,
                "http_status": http_status,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "catalog_ids": catalog_ids,
            }

    batch_outcome_counts: dict[str, int] = {}
    batch_failure_samples = []
    for seed in batch:
        outcome = outcomes.get(_source_key(seed), {})
        status = str(outcome.get("status") or "missing_outcome")
        batch_outcome_counts[status] = batch_outcome_counts.get(status, 0) + 1
        if status != "metadata_listing_processed" and len(batch_failure_samples) < 5:
            batch_failure_samples.append({
                "source_url": seed.get("source_url"),
                "status": status,
                "reason": outcome.get("reason"),
                "http_status": outcome.get("http_status"),
            })

    result = {
        "schema_version": 2,
        "status": "metadata_only_staging_not_production",
        "last_batch_outcome_counts": batch_outcome_counts,
        "last_batch_failure_samples": batch_failure_samples,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue_matches_current_index": queue_status["exact_match"],
        "queue_snapshot_compatible_with_current_index": True,
        "queue_source_index_sha_matches_current": queue_status["exact_match"],
        "queue_source_index_page_lag": queue_status["page_lag"],
        "queue_source_index_age_seconds": queue_status["age_seconds"],
        "queue_source_index_pages": queue_pages,
        "queue_source_index_sha256": queue_sha,
        "queue_source_index_updated_at": queue_updated_at,
        "source_index_pages": current_index["pages"] if current_index else None,
        "source_index_sha256": current_index["sha256"] if current_index else None,
        "eligible_catalog_landing_pages": len(eligible),
        "last_batch_size": len(batch),
        "last_batch_processed": processed,
        "last_batch_new_unique_child_records": discovered,
        "next_source_offset": next_cursor,
        "source_outcomes": outcomes,
        "records": list(records_by_id.values()),
        "metrics": {
            "unique_child_records_staged": len(records_by_id),
            "catalog_memberships": sum(len(row.get("catalog_ids", [])) for row in records_by_id.values()),
            "catalog_landing_pages_processed": sum(
                1 for value in outcomes.values()
                if isinstance(value, dict) and value.get("status") == "metadata_listing_processed"
            ),
            "verified_catalogs": 0,
            "records_published": 0,
            "child_pages_fetched": 0,
            "full_text_fetched": 0,
            "media_fetched": 0,
        },
    }
    if not eligible:
        result["status"] = "no_policy_preapproved_catalog_landing_pages"
    elif not batch:
        result["status"] = "no_unprocessed_catalog_landing_pages"
    if not batch:
        # Nothing changed: preserve the prior checkpoint and avoid a
        # timestamp-only commit on every scheduled cycle.
        return result
    _write_payload(state_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=QUEUE)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    args = parser.parse_args()
    result = run_batch(args.queue, args.state, args.limit, args.index)
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "status",
                    "queue_matches_current_index",
                    "queue_snapshot_compatible_with_current_index",
                    "queue_source_index_page_lag",
                    "queue_source_index_age_seconds",
                    "eligible_catalog_landing_pages",
                    "last_batch_size",
                    "last_batch_processed",
                    "last_batch_new_unique_child_records",
                    "last_batch_outcome_counts",
                    "last_batch_failure_samples",
                    "metrics",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
