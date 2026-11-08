#!/usr/bin/env python3
"""Policy-gated JSON API connector runner. Downloads only explicitly reviewed sources."""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path


def nested(value, path):
    for part in path.split(".") if path else []:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def fetch_page(url, timeout=30):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "EverythingHubResearchBot/1.0 (source contact in project documentation)",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("source returned HTTP " + str(response.status))
        return json.loads(response.read().decode("utf-8"))


def acquire(config_path, source_id, output_path, max_pages):
    registry = json.loads(config_path.read_text(encoding="utf-8"))
    source = next((item for item in registry["connectors"]
                   if item["source_id"] == source_id), None)
    if source is None:
        raise ValueError("unknown connector: " + source_id)
    if (not source.get("enabled") or source.get("review_status") != "reviewed_allowed"
            or source.get("source_status") != "active"):
        raise ValueError("connector is disabled, inactive, or not approved; no network request made")
    if not source.get("terms_url"):
        raise ValueError("approved connector must include reviewed terms_url")
    api = source.get("api")
    if not isinstance(api, dict) or not api.get("endpoint") or not api.get("records_path"):
        raise ValueError("approved connector still needs a reviewed API endpoint and records_path")
    page_size = min(int(api.get("page_size", 100)), 500)
    cursor_param = api.get("cursor_param", "cursor")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path.with_suffix(output_path.suffix + ".checkpoint.json")
    fingerprint = hashlib.sha256(json.dumps({
        "source_id": source_id, "api": api, "terms_url": source["terms_url"]
    }, sort_keys=True).encode("utf-8")).hexdigest()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {}
    if checkpoint and checkpoint.get("config_fingerprint") != fingerprint:
        raise ValueError("connector configuration changed; use a new output file or clear its checkpoint")
    committed_bytes = int(checkpoint.get("output_bytes", 0))
    if output_path.exists():
        with output_path.open("r+b") as staged:
            staged.truncate(committed_bytes)
    elif committed_bytes:
        raise ValueError("checkpoint references missing staged output")
    cursor = checkpoint.get("next_cursor", source.get("discovery_cursor"))
    next_url = checkpoint.get("next_url")
    if checkpoint.get("complete"):
        print(json.dumps({"source_id": source_id, "pages": 0, "records_staged": 0,
                          "complete": True, "published_to_production": False}, indent=2))
        return 0
    count = 0
    pages = 0
    delay = max(float(source.get("request_policy", {}).get("min_delay_ms", 1000)) / 1000, 0.1)
    with output_path.open("a", encoding="utf-8") as out:
        while pages < max_pages:
            if next_url:
                url = next_url
                parsed = urlsplit(url)
                allowed_hosts = set(api.get("allowed_next_hosts", []))
                if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
                    raise ValueError("pagination next URL is outside the reviewed HTTPS host allowlist")
            else:
                params = dict(api.get("fixed_params", {}))
                params[api.get("page_size_param", "limit")] = page_size
                if cursor:
                    params[cursor_param] = cursor
                url = api["endpoint"] + ("&" if "?" in api["endpoint"] else "?") + urllib.parse.urlencode(params)
            payload = fetch_page(url)
            records = nested(payload, api["records_path"])
            if not isinstance(records, list):
                raise ValueError("configured records_path did not resolve to a list")
            for record in records:
                if not isinstance(record, dict):
                    continue
                out.write(json.dumps({
                    "source_id": source_id,
                    "source_record": record,
                    "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "license": source.get("license"),
                    "attribution": source.get("attribution_text", source["name"]),
                }, ensure_ascii=False) + "\n")
                count += 1
            next_url = nested(payload, api.get("next_url_path")) if api.get("next_url_path") else None
            cursor = nested(payload, api.get("next_cursor_path", "next_cursor")) if not api.get("next_url_path") else None
            pages += 1
            out.flush()
            checkpoint = {
                "source_id": source_id,
                "next_cursor": cursor,
                "next_url": next_url,
                "config_fingerprint": fingerprint,
                "output_bytes": out.tell(),
                "pages_committed": int(checkpoint.get("pages_committed", 0)) + 1,
                "complete": (not next_url if api.get("next_url_path") else not cursor) or not records,
            }
            checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
            if checkpoint["complete"]:
                break
            time.sleep(delay)
    print(json.dumps({"source_id": source_id, "pages": pages, "records_staged": count,
                      "next_cursor": cursor, "next_url": next_url, "checkpoint": str(checkpoint_path),
                      "output": str(output_path), "published_to_production": False}, indent=2))
    return count

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("connectors/registry.json"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=5)
    args = parser.parse_args()
    if args.max_pages < 1:
        parser.error("--max-pages must be positive")
    acquire(args.registry, args.source, args.output, args.max_pages)


if __name__ == "__main__":
    main()
