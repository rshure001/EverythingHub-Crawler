#!/usr/bin/env python3
"""Probe known public host endpoints for machine-readable catalog interfaces.

Only HTTPS URLs on each already-discovered host are requested. Redirects to
other hosts or to HTTP are rejected. This records evidence; it does not ingest
records or claim a catalog is verified merely because a URL responds.
"""
from __future__ import annotations
import argparse, json, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from catalog_source_policy import SourcePolicyError, policy_for_host, request_source_bytes

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "host-catalog-candidates.json"
OUTPUT = ROOT / "data" / "host-catalog-probes.json"
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


def rundef run(input_path: Path, output_path: Path, limit: int, offset: int, pause: float) -> dict:
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
    print(json.dumps({k: output[k] for k in ("hosts_in_inventory", "last_batch_offset", "last_batch_hosts_probed", "hosts_probed", "verified_interface_hosts", "verified_interfaces")}, indent=2))
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--pause", type=float, default=0.2)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or args.offset < 0 or not 0 <= args.pause <= 5:
        parser.error("limit must be 1..100, offset >= 0, pause 0..5")
    run(args.input, args.output, args.limit, args.offset, args.pause)
