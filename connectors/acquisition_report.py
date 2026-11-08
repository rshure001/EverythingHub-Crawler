#!/usr/bin/env python3
"""Build a truthful acquisition scorecard from registry, staging files, and checkpoints."""
import argparse
import json
from collections import Counter
from pathlib import Path


def read_json(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_staged(path):
    accepted = 0
    malformed = 0
    licenses = Counter()
    for line in path.open("r", encoding="utf-8"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        accepted += 1
        licenses[row.get("license") or "unspecified"] += 1
    return accepted, malformed, dict(sorted(licenses.items()))


def build_report(registry_path, staging_dir):
    registry = read_json(registry_path)
    sources = []
    totals = Counter()
    for source in registry.get("connectors", []):
        source_id = source["source_id"]
        staged_path = staging_dir / (source_id + ".jsonl")
        checkpoint_path = staged_path.with_suffix(staged_path.suffix + ".checkpoint.json")
        checkpoint = read_json(checkpoint_path)
        staged, malformed, licenses = inspect_staged(staged_path) if staged_path.exists() else (0, 0, {})
        registered = source.get("counts", {})
        row = {
            "source_id": source_id,
            "name": source.get("name"),
            "lane": source.get("lane"),
            "enabled": bool(source.get("enabled")),
            "review_status": source.get("review_status", "not_reviewed"),
            "source_status": source.get("source_status", "proposed"),
            "pages_committed": int(checkpoint.get("pages_committed", 0)),
            "records_staged": staged,
            "records_rejected": int(checkpoint.get("records_rejected", 0)),
            "malformed_staging_lines": malformed,
            "license_breakdown": licenses,
            "complete": bool(checkpoint.get("complete", False)),
            "indexed": int(registered.get("indexed", 0)),
            "searchable": int(registered.get("searchable", 0)),
        }
        sources.append(row)
        for key in ("pages_committed", "records_staged", "records_rejected",
                    "malformed_staging_lines", "indexed", "searchable"):
            totals[key] += row[key]
    return {
        "report_version": 1,
        "production_publishing_enabled": bool(registry.get("production_publishing", False)),
        "totals": dict(totals),
        "sources": sources,
        "notes": [
            "Staged records are not indexed or searchable records.",
            "Indexed/searchable counts come only from registry counters and are not inferred from staging.",
            "This report performs no network requests and makes no production writes."
        ]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("connectors/registry.json"))
    parser.add_argument("--staging-dir", type=Path, default=Path("staging/connectors"))
    parser.add_argument("--output", type=Path, default=Path("staging/connectors/acquisition-report.json"))
    args = parser.parse_args()
    report = build_report(args.registry, args.staging_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
