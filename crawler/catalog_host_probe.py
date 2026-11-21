#!/usr/bin/env python3
"""Probe known public host endpoints for machine-readable catalog interfaces.

Only source-terms-approved HTTPS API paths are requested. Every request is
robots.txt checked, rate limited, DNS pinned, and restricted to same-origin
redirects. This records evidence; it does not ingest or verify a catalog.
"""
from __future__ import annotations
import argparse, hashlib, json, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from catalog_source_policy import SourcePolicyError, authorize_source_url, policy_for_host, request_source_bytes
from catalog_host_review_queue import validate_review_queue

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "host-catalog-candidates.json"
OUTPUT = ROOT / "data" / "host-catalog-probes.json"
LANDING_QUEUE = ROOT / "data" / "page-derived-catalogs" / "source-host-review-queue.json"
CATALOG_MANIFEST = ROOT / "data" / "page-derived-catalogs" / "manifest.json"
LANDING_ROBOTS_OUTPUT = ROOT / "data" / "catalog-landing-page-robots.json"
ROBOTS_RECHECK_SECONDS = 86_400
MAX_BYTES = 1_500_000
TIMEOUT = 8
def probe_one(host: str, kind: str, path: str) -> dict:
    url = "https://" + host + path
    policy = policy_for_host(host)
    if not policy or policy.get("discovery_allowed") is not True:
        return {"kind": kind, "url": url, "status": "source_terms_unreviewed"}
    try:
        raw, status, headers, _policy = request_source_bytes(
            url, purpose="probe", max_bytes=MAX_BYTES, timeout=TIMEOUT,
            accept="application/json",
        )
        if status != 200:
            return {"kind": kind, "url": url, "status": "http_error", "http_status": status}
        ctype = headers.get("Content-Type", "").split(";")[0].lower()
        result = {"kind": kind, "url": url, "status": "responded", "http_status": status,
                  "content_type": ctype, "bytes": len(raw)}
        if kind == "ckan_api":
            try:
                payload = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("success") is True:
                    result["evidence"] = "ckan_package_search"
                    data = payload.get("result") or {}
                    result["dataset_total"] = data.get("count") if isinstance(data, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                result["status"] = "invalid_json"
        return result
    except SourcePolicyError as exc:
        return {"kind": kind, "url": url, "status": "source_policy_blocked", "reason": str(exc)[:240]}
    except urllib.error.HTTPError as exc:
        return {"kind": kind, "url": url, "status": "http_error", "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"kind": kind, "url": url, "status": type(exc).__name__}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _landing_policy_fingerprint(item: dict) -> str:
    keys = (
        "source_url", "source_access_mode", "review_status", "terms_url",
        "terms_status", "reviewed_at", "allowed_catalog_path_patterns",
        "metadata_only", "allowed_metadata_fields", "prohibited_content",
        "max_response_bytes", "min_interval_seconds", "max_requests_per_run",
    )
    payload = {key: item.get(key) for key in keys}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_preapproved_landing_url(item: object) -> bool:
    return (
        isinstance(item, dict)
        and item.get("review_status") == "policy_preapproved_robots_recheck_required"
        and item.get("source_access_mode") == "catalog_landing_page"
        and item.get("metadata_only") is True
        and item.get("robots_recheck_required") is True
        and item.get("fetch_authorized") is False
        and isinstance(item.get("source_url"), str)
    )


def probe_catalog_landing_page_robots(item: dict) -> dict:
    """Check source policy and robots for one exact landing URL; never fetch page content."""
    url = item.get("source_url")
    base = {
        "source_url": url,
        "source_host": item.get("source_host"),
        "catalog_ids": item.get("catalog_ids", []),
        "catalog_count": item.get("catalog_count", 0),
        "source_access_mode": "catalog_landing_page",
        "review_status": item.get("review_status"),
        "terms_url": item.get("terms_url"),
        "terms_status": item.get("terms_status"),
        "reviewed_at": item.get("reviewed_at"),
        "allowed_catalog_path_patterns": item.get("allowed_catalog_path_patterns", []),
        "metadata_only": True,
        "allowed_metadata_fields": item.get("allowed_metadata_fields", []),
        "prohibited_content": item.get("prohibited_content", []),
        "max_response_bytes": item.get("max_response_bytes"),
        "min_interval_seconds": item.get("min_interval_seconds"),
        "max_requests_per_run": item.get("max_requests_per_run"),
        "fetch_authorized": False,
        "page_content_fetched": False,
        "page_requests": 0,
        "robots_check_performed": False,
        "checked_at": _utc_now(),
    }
    if not _is_preapproved_landing_url(item):
        return {**base, "status": "not_preapproved", "robots_allowed": None}
    try:
        policy, _parser, delay, _allowed_paths = authorize_source_url(
            url, purpose="landing_page"
        )
        return {
            **base,
            "status": "robots_allowed",
            "robots_allowed": True,
            "robots_check_performed": True,
            "effective_min_interval_seconds": delay,
            "policy_max_requests_per_run": int(policy.get("max_requests_per_run", 1)),
        }
    except SourcePolicyError as exc:
        reason = str(exc)[:240]
        if reason.startswith("robots.txt disallows"):
            status, allowed, checked = "robots_denied", False, True
        elif reason.startswith("robots.txt could not be safely verified"):
            status, allowed, checked = "robots_unverified", None, True
        else:
            status, allowed, checked = "source_policy_blocked", None, False
        return {
            **base,
            "status": status,
            "robots_allowed": allowed,
            "robots_check_performed": checked,
            "reason": reason,
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {
            **base,
            "status": "robots_unverified",
            "robots_allowed": None,
            "robots_check_performed": True,
            "reason": type(exc).__name__,
        }


def run_landing_page_robots(
    queue_path: Path,
    output_path: Path,
    *,
    limit: int = 30,
    recheck_seconds: int = ROBOTS_RECHECK_SECONDS,
    manifest_path: Path | None = None,
) -> dict:
    """Robots-only preflight for preapproved catalog landing URLs; never fetch pages."""
    if not 0 <= limit <= 100 or recheck_seconds < 0:
        raise ValueError("landing robots limit must be 0..100 and recheck_seconds non-negative")
    if not queue_path.is_file() or limit == 0:
        output = {
            "schema_version": 1,
            "status": "robots_preflight_only_not_fetch_authorization",
            "generated_at": _utc_now(),
            "analysis_version": None,
            "source_index_pages": None,
            "source_index_updated_at": None,
            "source_index_sha256": None,
            "eligible_catalog_landing_urls": 0,
            "last_batch_urls_checked": 0,
            "urls_checked": 0,
            "robots_checks_performed": 0,
            "robots_allowed": 0,
            "robots_denied": 0,
            "robots_unverified": 0,
            "source_policy_blocked": 0,
            "fetch_authorized_urls": 0,
            "page_content_fetched_urls": 0,
            "page_requests": 0,
            "results": [],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.exists():
            output_path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        return {
            "status": "queue_missing" if not queue_path.is_file() else "disabled",
            "last_batch_urls_checked": 0,
            "urls_checked": 0,
            "fetch_authorized_urls": 0,
            "page_content_fetched_urls": 0,
            "page_requests": 0,
        }
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    if not isinstance(queue, dict) or queue.get("status") != "source_review_backlog_not_a_fetch_queue":
        raise ValueError("landing robots preflight requires the validated non-fetch source review backlog")
    metrics = queue.get("metrics")
    if not isinstance(metrics, dict) or any(
        metrics.get(key) != 0
        for key in ("robots_checks_performed", "network_requests", "fetch_authorized_urls", "records_published")
    ):
        raise ValueError("source review backlog must remain an unprobed, non-fetch queue")
    candidates = queue.get("candidate_urls")
    if not isinstance(candidates, list):
        raise ValueError("source review candidate_urls must be an array")

    manifest_path = manifest_path or CATALOG_MANIFEST
    if not manifest_path.is_file():
        raise ValueError("catalog manifest is required to validate the source review backlog")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_review_queue(queue, manifest)

    previous = {}
    if output_path.is_file():
        prior = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(prior, dict) or prior.get("status") != "robots_preflight_only_not_fetch_authorization":
            raise ValueError("existing landing robots ledger has an unsupported status")
        previous = {
            row.get("source_url"): row
            for row in prior.get("results", [])
            if isinstance(row, dict) and isinstance(row.get("source_url"), str)
        }

    eligible = [row for row in candidates if _is_preapproved_landing_url(row)]
    eligible_urls = {row["source_url"] for row in eligible}
    previous = {url: row for url, row in previous.items() if url in eligible_urls}
    now = datetime.now(timezone.utc)
    pending = []
    for item in eligible:
        fingerprint = _landing_policy_fingerprint(item)
        prior = previous.get(item["source_url"])
        if (
            prior
            and prior.get("policy_fingerprint") == fingerprint
            and prior.get("review_status") == item.get("review_status")
        ):
            try:
                checked = datetime.fromisoformat(str(prior.get("checked_at", "")).replace("Z", "+00:00"))
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=timezone.utc)
                if (now - checked).total_seconds() < recheck_seconds:
                    continue
            except (TypeError, ValueError):
                pass
        pending.append((item, fingerprint))

    selected = pending[:limit]
    for item, fingerprint in selected:
        result = probe_catalog_landing_page_robots(item)
        result["policy_fingerprint"] = fingerprint
        result["analysis_version"] = queue.get("analysis_version")
        result["source_index_sha256"] = queue.get("source_index_sha256")
        previous[item["source_url"]] = result

    cumulative = sorted(previous.values(), key=lambda row: (row.get("source_host", ""), row.get("source_url", "")))
    counts = {
        status: sum(row.get("status") == status for row in cumulative)
        for status in ("robots_allowed", "robots_denied", "robots_unverified", "source_policy_blocked")
    }
    output = {
        "schema_version": 1,
        "status": "robots_preflight_only_not_fetch_authorization",
        "generated_at": _utc_now(),
        "analysis_version": queue.get("analysis_version"),
        "source_index_pages": queue.get("source_index_pages"),
        "source_index_updated_at": queue.get("source_index_updated_at"),
        "source_index_sha256": queue.get("source_index_sha256"),
        "eligible_catalog_landing_urls": len(eligible),
        "last_batch_urls_checked": len(selected),
        "urls_checked": len(cumulative),
        "robots_checks_performed": sum(bool(row.get("robots_check_performed")) for row in cumulative),
        "robots_allowed": counts["robots_allowed"],
        "robots_denied": counts["robots_denied"],
        "robots_unverified": counts["robots_unverified"],
        "source_policy_blocked": counts["source_policy_blocked"],
        "fetch_authorized_urls": 0,
        "page_content_fetched_urls": 0,
        "page_requests": 0,
        "results": cumulative,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(output_path)
    return {
        key: output[key]
        for key in (
            "eligible_catalog_landing_urls", "last_batch_urls_checked", "urls_checked",
            "robots_checks_performed", "robots_allowed", "robots_denied",
            "robots_unverified", "source_policy_blocked", "fetch_authorized_urls",
            "page_content_fetched_urls", "page_requests",
        )
    }


def run(input_path: Path, output_path: Path, limit: int, offset: int, pause: float, *, landing_queue_path: Path | None = None, landing_output_path: Path | None = None, landing_manifest_path: Path | None = None, landing_limit: int = 30, robots_recheck_seconds: int = ROBOTS_RECHECK_SECONDS) -> dict:
    inventory = json.loads(input_path.read_text(encoding="utf-8"))
    hosts = inventory.get("host_candidates", [])
    ordered = sorted(hosts, key=lambda x: (-sum((x.get("hint_counts") or {}).values()), -x.get("source_pages", 0), x.get("host", "")))
    batch = ordered[offset:offset + limit]
    results = []
    for item in batch:
        host = item.get("host")
        if not isinstance(host, str) or not host or "/" in host:
            continue
        policy = policy_for_host(host)
        if not policy or policy.get("discovery_allowed") is not True:
            evidence = [{"kind": "source_policy", "url": "https://" + host, "status": "source_terms_unreviewed"}]
        else:
            evidence = [
                probe_one(host, "ckan_api", path + "?q=%2A%3A%2A&rows=1")
                for path in policy.get("allowed_probe_paths", [])
            ]
            if not evidence:
                evidence = [{"kind": "source_policy", "url": "https://" + host, "status": "no_probe_path_approved"}]
        verified = [x for x in evidence if x.get("evidence")]
        results.append({"host": host, "source_pages": item.get("source_pages", 0), "probes": evidence, "verified_interfaces": verified, "status": "interface_evidence_found" if verified else "no_interface_verified"})
        time.sleep(pause)
    # Merge batches so the inventory accumulates evidence for every host.
    previous = {}
    if output_path.exists():
        try:
            prior = json.loads(output_path.read_text(encoding="utf-8"))
            previous = {x.get("host"): x for x in prior.get("results", [])
                        if isinstance(x, dict) and x.get("host")}
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous.update({x["host"]: x for x in results})
    cumulative = sorted(previous.values(), key=lambda x: x.get("host", ""))
    output = {
        "schema_version": 1,
        "strategy": "bounded-same-host-https-probes-cumulative",
        "source_inventory_pages": inventory.get("source_index_pages", 0),
        "hosts_in_inventory": len(hosts),
        "last_batch_offset": offset,
        "last_batch_limit": limit,
        "last_batch_hosts_probed": len(results),
        "hosts_probed": len(cumulative),
        "verified_interface_hosts": sum(bool(x["verified_interfaces"]) for x in cumulative),
        "verified_interfaces": sum(len(x["verified_interfaces"]) for x in cumulative),
        "results": cumulative,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(output_path)
    summary = {k: output[k] for k in ("hosts_in_inventory", "last_batch_offset", "last_batch_hosts_probed", "hosts_probed", "verified_interface_hosts", "verified_interfaces")}
    if landing_queue_path is not None and landing_output_path is not None:
        summary["catalog_landing_page_robots"] = run_landing_page_robots(
            landing_queue_path,
            landing_output_path,
            limit=landing_limit,
            recheck_seconds=robots_recheck_seconds,
            manifest_path=landing_manifest_path,
        )
    print(json.dumps(summary, indent=2))
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--landing-limit", type=int, default=30)
    parser.add_argument("--robots-recheck-seconds", type=int, default=ROBOTS_RECHECK_SECONDS)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or args.offset < 0 or not 0 <= args.pause <= 5:
        parser.error("limit must be 1..100, offset >= 0, pause 0..5")
    if not 0 <= args.landing_limit <= 100 or args.robots_recheck_seconds < 0:
        parser.error("landing-limit must be 0..100 and robots-recheck-seconds non-negative")
    run(args.input, args.output, args.limit, args.offset, args.pause, landing_queue_path=LANDING_QUEUE, landing_output_path=LANDING_ROBOTS_OUTPUT, landing_manifest_path=CATALOG_MANIFEST, landing_limit=args.landing_limit, robots_recheck_seconds=args.robots_recheck_seconds)
