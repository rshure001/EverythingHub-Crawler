#!/usr/bin/env python3
"""Fail-closed source-terms, robots.txt, redirect, and rate-limit gate for catalog APIs."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from crawl import (
    UA,
    RobotsAwareRedirectHandler,
    effective_host_delay,
    load_robots_policy,
    robots_can_fetch,
    same_origin,
)
from safe_http import build_pinned_opener

REGISTRY_PATH = Path(__file__).with_name("catalog-source-terms.json")
_REVIEWED_STATUSES = {"reviewed", "reviewed_per_dataset"}
_REGISTRY = None
_ROBOTS_CACHE = {}
_LAST_REQUEST_AT = {}
_REQUEST_COUNTS = {}


class SourcePolicyError(ValueError):
    """Raised when source terms or robots.txt do not authorize a catalog request."""


def load_registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if payload.get("default_policy") != "deny" or not isinstance(payload.get("sources"), dict):
            raise SourcePolicyError("catalog source policy registry must use a deny-by-default sources map")
        _REGISTRY = payload
    return _REGISTRY


def policy_for_host(host: str) -> dict | None:
    if not isinstance(host, str) or not host:
        return None
    value = load_registry()["sources"].get(host.lower().rstrip("."))
    return dict(value) if isinstance(value, dict) else None


def path_allowed(path: str, prefixes: list[str]) -> bool:
    if not isinstance(path, str) or not isinstance(prefixes, list):
        return False
    return any(
        isinstance(prefix, str)
        and prefix.startswith("/")
        and (path == prefix or path.startswith(prefix.rstrip("/") + "/"))
        for prefix in prefixes
    )


def authorize_source_url(url: str, purpose: str = "discovery") -> tuple[dict, object, float, list[str]]:
    parsed = urllib.parse.urlsplit(url)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise SourcePolicyError("invalid source URL port") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SourcePolicyError("catalog source URLs must be credential-free HTTPS URLs")
    host = parsed.hostname.lower().rstrip(".")
    policy = policy_for_host(host)
    if not policy or policy.get("discovery_allowed") is not True:
        raise SourcePolicyError(f"source terms do not authorize discovery for {host}")
    if policy.get("terms_status") not in _REVIEWED_STATUSES or not policy.get("terms_url"):
        raise SourcePolicyError(f"source terms are not reviewed for {host}")
    if purpose == "discovery":
        allowed_paths = policy.get("allowed_api_paths") or []
    elif purpose == "probe":
        allowed_paths = policy.get("allowed_probe_paths") or []
    else:
        raise SourcePolicyError("unsupported catalog request purpose")
    if not path_allowed(parsed.path, allowed_paths):
        raise SourcePolicyError(f"path is outside the approved {purpose} allowlist for {host}")
    parser = _ROBOTS_CACHE.get(host)
    if parser is None:
        try:
            parser = load_robots_policy("https://" + host)
        except Exception as exc:
            raise SourcePolicyError(f"robots.txt could not be safely verified for {host}: {type(exc).__name__}") from exc
        _ROBOTS_CACHE[host] = parser
    if not robots_can_fetch(parser, UA, url):
        raise SourcePolicyError(f"robots.txt disallows {parsed.path} on {host}")
    delay = max(float(policy.get("min_interval_seconds", 1.0)), float(effective_host_delay(parser)))
    return policy, parser, delay, list(allowed_paths)


def request_source_bytes(
    url: str,
    *,
    purpose: str = "discovery",
    max_bytes: int = 8_000_000,
    timeout: int = 20,
    accept: str = "application/json",
) -> tuple[bytes, int, dict, dict]:
    policy, parser, delay, allowed_paths = authorize_source_url(url, purpose)
    host = urllib.parse.urlsplit(url).hostname.lower().rstrip(".")
    count = _REQUEST_COUNTS.get(host, 0)
    if count >= int(policy.get("max_requests_per_run", 1)):
        raise SourcePolicyError(f"per-run request budget exhausted for {host}")
    now = time.monotonic()
    previous = _LAST_REQUEST_AT.get(host)
    if previous is not None:
        wait = delay - (now - previous)
        if wait > 0:
            time.sleep(wait)
    _LAST_REQUEST_AT[host] = time.monotonic()
    _REQUEST_COUNTS[host] = count + 1

    def redirect_allowed(target: str) -> bool:
        return (
            same_origin(url, target)
            and path_allowed(urllib.parse.urlsplit(target).path, allowed_paths)
            and robots_can_fetch(parser, UA, target)
        )

    opener = build_pinned_opener(RobotsAwareRedirectHandler(redirect_allowed))
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        if not same_origin(url, final_url):
            raise SourcePolicyError("cross-origin catalog API redirect blocked")
        final = urllib.parse.urlsplit(final_url)
        if not path_allowed(final.path, allowed_paths) or not robots_can_fetch(parser, UA, final_url):
            raise SourcePolicyError("catalog API redirect target is outside source policy or robots rules")
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise SourcePolicyError("catalog API response exceeded the configured size limit")
        return raw, int(response.status), dict(response.headers.items()), policy


def reset_policy_state_for_tests() -> None:
    _ROBOTS_CACHE.clear()
    _LAST_REQUEST_AT.clear()
    _REQUEST_COUNTS.clear()
