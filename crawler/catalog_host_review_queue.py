#!/usr/bin/env python3
"""Build a fail-closed review backlog for page-derived catalog source URLs.

This is deliberately NOT a fetch queue. It makes no network requests, does not
check robots.txt, and never authorizes ingestion. Runtime crawling must still
pass catalog_source_policy.authorize_source_url before any API request.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data" / "page-derived-catalogs"
DEFAULT_MANIFEST = DEFAULT_DIR / "manifest.json"
DEFAULT_REGISTRY = DEFAULT_DIR / "registry.json.gz"
DEFAULT_POLICY = Path(__file__).with_name("catalog-source-terms.json")
DEFAULT_OUTPUT = DEFAULT_DIR / "source-host-review-queue.json"
REVIEWED_STATUSES = {"reviewed", "reviewed_per_dataset"}


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _canonical_https_url(value: object) -> tuple[str | None, str | None, str | None]:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 4096:
        return None, None, "missing_or_invalid_url"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None, None, "invalid_url"
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None, None, "https_without_credentials_required"
    host = parsed.hostname.lower().rstrip(".")
    if (
        not host
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        return None, None, "non_public_host"
    netloc = host if port in (None, 443) else f"{host}:{port}"
    canonical = urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
    return canonical, host, None


def _path_allowed(path: str, prefixes: object) -> bool:
    if not isinstance(prefixes, list):
        return False
    return any(
        isinstance(prefix, str)
        and prefix.startswith("/")
        and (path == prefix or path.startswith(prefix.rstrip("/") + "/"))
        for prefix in prefixes
    )


def _policy_status(host: str, path: str, sources: dict) -> tuple[str, dict]:
    policy = sources.get(host)
    if not isinstance(policy, dict):
        return "policy_review_required", {}
    if policy.get("discovery_allowed") is not True:
        return "discovery_disabled", policy
    if (
        policy.get("terms_status") not in REVIEWED_STATUSES
        or not policy.get("terms_url")
        or not policy.get("reviewed_at")
    ):
        return "terms_unreviewed", policy
    if not _path_allowed(path, policy.get("allowed_api_paths")):
        return "path_not_allowlisted", policy
    return "policy_preapproved_robots_recheck_required", policy


def load_verified_registry(manifest_path: Path, registry_path: Path) -> tuple[dict, dict]:
    manifest = _read_json(manifest_path)
    if manifest.get("version") != 1 or manifest.get("integrity") != "sha256":
        raise ValueError("catalog manifest version or integrity declaration is unsupported")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("catalog manifest files must be an array")
    entry = next((x for x in entries if isinstance(x, dict) and x.get("file") == "registry.json.gz"), None)
    if not entry:
        raise ValueError("catalog manifest does not list registry.json.gz")
    raw = registry_path.read_bytes()
    if len(raw) != entry.get("bytes"):
        raise ValueError("catalog registry byte length does not match its manifest")
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        raise ValueError("catalog registry SHA-256 does not match its manifest")
    registry = json.loads(gzip.decompress(raw).decode("utf-8"))
    if not isinstance(registry, dict):
        raise ValueError("catalog registry must contain a JSON object")
    if registry.get("status") != "candidate_staging_not_production":
        raise ValueError("catalog registry is not explicitly marked candidate-only")
    if registry.get("analysis_version") != manifest.get("analysis_version"):
        raise ValueError("catalog registry and manifest analysis versions differ")
    if registry.get("metrics") != manifest.get("metrics"):
        raise ValueError("catalog registry and manifest metrics differ")
    catalogs = registry.get("catalogs")
    if not isinstance(catalogs, list) or len(catalogs) != manifest.get("catalog_count"):
        raise ValueError("catalog registry count does not match its manifest")
    return manifest, registry


def build_review_queue(manifest: dict, registry: dict, policy_registry: dict) -> dict:
    if policy_registry.get("default_policy") != "deny":
        raise ValueError("source policy registry must remain deny-by-default")
    sources = policy_registry.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("source policy registry must contain a sources object")

    candidates = []
    url_groups = defaultdict(list)
    host_groups = defaultdict(list)
    status_counts = Counter()
    invalid_count = 0

    for catalog in registry.get("catalogs", []):
        if not isinstance(catalog, dict) or catalog.get("catalog_definition") is not True:
            continue
        catalog_id = catalog.get("catalog_id")
        title = catalog.get("title")
        if not isinstance(catalog_id, str) or not catalog_id or not isinstance(title, str) or not title:
            status = "invalid_catalog_identity"
            invalid_count += 1
            status_counts[status] += 1
            continue

        canonical, host, url_error = _canonical_https_url(catalog.get("source_url"))
        declared_host = str(catalog.get("source_host") or "").lower().rstrip(".")
        if url_error:
            status = url_error
            invalid_count += 1
            policy = {}
        elif declared_host and declared_host != host:
            status = "source_host_mismatch"
            invalid_count += 1
            policy = {}
        else:
            parsed = urlsplit(canonical)
            status, policy = _policy_status(host, parsed.path, sources)

        status_counts[status] += 1
        item = {
            "catalog_id": catalog_id,
            "title": title,
            "catalog_type": catalog.get("catalog_type"),
            "record_type": catalog.get("record_type"),
            "catalog_origin": catalog.get("catalog_origin"),
            "evidence_strength": catalog.get("evidence_strength"),
            "catalog_status": catalog.get("catalog_status") or catalog.get("status"),
            "record_count": int(catalog.get("record_count") or 0),
            "source_page_count": int(catalog.get("source_page_count") or 0),
            "source_host": host or declared_host or None,
            "source_url": canonical,
            "review_status": status,
            "terms_url": policy.get("terms_url"),
            "terms_status": policy.get("terms_status", "unreviewed"),
            "reviewed_at": policy.get("reviewed_at"),
            "per_dataset_rights_required": bool(policy.get("per_dataset_rights_required", True)),
            "robots_recheck_required": bool(canonical),
            "fetch_authorized": False,
        }
        candidates.append(item)
        if canonical and host:
            url_groups[canonical].append(item)
            host_groups[host].append(item)

    candidate_urls = []
    for url, rows in url_groups.items():
        statuses = sorted({row["review_status"] for row in rows})
        status = statuses[0] if len(statuses) == 1 else "mixed_policy_statuses"
        if status == "policy_preapproved_robots_recheck_required" and len(statuses) == 1:
            policy_preapproved = True
        else:
            policy_preapproved = False
        sample = rows[0]
        candidate_urls.append({
            "source_url": url,
            "source_host": sample["source_host"],
            "catalog_ids": sorted({row["catalog_id"] for row in rows}),
            "catalog_count": len({row["catalog_id"] for row in rows}),
            "review_status": status,
            "policy_preapproved": policy_preapproved,
            "robots_recheck_required": True,
            "fetch_authorized": False,
            "terms_url": sample.get("terms_url"),
            "terms_status": sample.get("terms_status", "unreviewed"),
            "per_dataset_rights_required": any(row["per_dataset_rights_required"] for row in rows),
        })

    hosts = []
    for host, rows in host_groups.items():
        host_statuses = Counter(row["review_status"] for row in rows)
        unique_urls = {row["source_url"] for row in rows}
        sample = rows[0]
        hosts.append({
            "host": host,
            "catalog_definition_count": len({row["catalog_id"] for row in rows}),
            "unique_source_urls": len(unique_urls),
            "review_status_counts": dict(sorted(host_statuses.items())),
            "terms_url": sample.get("terms_url"),
            "terms_status": sample.get("terms_status", "unreviewed"),
            "reviewed_at": sample.get("reviewed_at"),
            "per_dataset_rights_required": any(row["per_dataset_rights_required"] for row in rows),
            "sample_catalogs": [
                {
                    "catalog_id": row["catalog_id"],
                    "title": row["title"],
                    "catalog_type": row["catalog_type"],
                    "source_url": row["source_url"],
                    "review_status": row["review_status"],
                }
                for row in sorted(rows, key=lambda x: (x["title"].casefold(), x["catalog_id"]))[:10]
            ],
        })

    candidates.sort(key=lambda x: (str(x["source_host"] or ""), x["title"].casefold(), x["catalog_id"]))
    candidate_urls.sort(key=lambda x: (x["source_host"], x["source_url"]))
    hosts.sort(key=lambda x: (-x["catalog_definition_count"], x["host"]))

    url_status_counts = Counter(row["review_status"] for row in candidate_urls)
    preapproved_urls = url_status_counts["policy_preapproved_robots_recheck_required"]
    metrics = {
        "catalog_definition_candidates": len(candidates),
        "unique_source_hosts": len(hosts),
        "unique_source_urls": len(candidate_urls),
        "policy_preapproved_urls_needing_runtime_robots_check": preapproved_urls,
        "policy_review_required_urls": sum(
            count for status, count in url_status_counts.items()
            if status in {"policy_review_required", "discovery_disabled", "terms_unreviewed"}
        ),
        "path_not_allowlisted_urls": url_status_counts["path_not_allowlisted"],
        "mixed_policy_status_urls": url_status_counts["mixed_policy_statuses"],
        "invalid_catalog_candidates": invalid_count,
        "robots_checks_performed": 0,
        "network_requests": 0,
        "fetch_authorized_urls": 0,
        "records_published": 0,
    }
    return {
        "schema_version": 1,
        "status": "source_review_backlog_not_a_fetch_queue",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "analysis_version": manifest.get("analysis_version"),
        "source_index_pages": manifest.get("source_index_pages"),
        "source_index_updated_at": manifest.get("source_index_updated_at"),
        "source_index_sha256": manifest.get("source_index_sha256"),
        "metrics": metrics,
        "catalogs": candidates,
        "candidate_urls": candidate_urls,
        "hosts": hosts,
    }


def validate_review_queue(payload: dict, manifest: dict) -> dict:
    if payload.get("status") != "source_review_backlog_not_a_fetch_queue":
        raise ValueError("source review output must not be presented as a fetch queue")
    for key in ("analysis_version", "source_index_pages", "source_index_updated_at", "source_index_sha256"):
        if payload.get(key) != manifest.get(key):
            raise ValueError(f"source review output is stale or mismatched: {key}")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("source review metrics are missing")
    if any(metrics.get(key) != 0 for key in ("robots_checks_performed", "network_requests", "fetch_authorized_urls", "records_published")):
        raise ValueError("source review generation must not fetch, authorize, or publish records")
    catalogs = payload.get("catalogs")
    urls = payload.get("candidate_urls")
    if not isinstance(catalogs, list) or not isinstance(urls, list):
        raise ValueError("source review catalogs and candidate URLs must be arrays")
    catalog_ids = [row.get("catalog_id") for row in catalogs if isinstance(row, dict)]
    source_urls = [row.get("source_url") for row in urls if isinstance(row, dict)]
    if len(catalog_ids) != len(set(catalog_ids)):
        raise ValueError("source review backlog contains duplicate catalog IDs")
    if len(source_urls) != len(set(source_urls)):
        raise ValueError("source review backlog contains duplicate source URLs")
    if any(row.get("fetch_authorized") is not False for row in catalogs + urls):
        raise ValueError("source review backlog must not authorize fetching")
    if any(row.get("robots_recheck_required") is not True for row in urls):
        raise ValueError("every candidate URL must require a fresh runtime robots check")
    if metrics.get("catalog_definition_candidates") != len(catalogs):
        raise ValueError("source review candidate count does not match its rows")
    if metrics.get("unique_source_urls") != len(urls):
        raise ValueError("source review URL count does not match its rows")
    return {
        "catalog_definition_candidates": len(catalogs),
        "unique_source_hosts": len(payload.get("hosts", [])),
        "unique_source_urls": len(urls),
        "policy_preapproved_urls_needing_runtime_robots_check": metrics.get("policy_preapproved_urls_needing_runtime_robots_check", 0),
        "policy_review_required_urls": metrics.get("policy_review_required_urls", 0),
        "fetch_authorized_urls": 0,
        "network_requests": 0,
    }


def build_from_files(manifest_path: Path, registry_path: Path, policy_path: Path) -> tuple[dict, dict]:
    manifest, registry = load_verified_registry(manifest_path, registry_path)
    policy = _read_json(policy_path)
    return manifest, build_review_queue(manifest, registry, policy)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    manifest, expected = build_from_files(args.manifest, args.registry, args.policy)
    if args.validate_only:
        actual = _read_json(args.output)
        summary = validate_review_queue(actual, manifest)
        if actual.get("metrics") != expected.get("metrics") or actual.get("candidate_urls") != expected.get("candidate_urls"):
            raise ValueError("published source review backlog does not match the current registry and policy")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = validate_review_queue(expected, manifest)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
