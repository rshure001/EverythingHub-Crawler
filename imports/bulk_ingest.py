#!/usr/bin/env python3
"""Safe, restartable staging importer for approved bulk JSONL/CSV datasets.

This tool writes normalized JSONL staging records only. It does not publish to production.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

LANES = {"general", "science", "news", "technology", "business",
         "government", "culture", "nature"}
REQUIRED_REVIEW = "reviewed_allowed"


def canonical_id(record):
    for key in ("canonical_url", "url", "identifier", "id"):
        value = str(record.get(key, "")).strip()
        if value:
            return value
    return ""


def normalize(record, source):
    source_id = source["source_id"]
    lane = source["lane"]
    identifier = canonical_id(record)
    if not identifier:
        raise ValueError("missing canonical identifier")
    title = str(record.get("title") or record.get("name") or "").strip()
    description = str(record.get("description") or record.get("summary") or "").strip()
    body = str(record.get("body_text") or record.get("text") or record.get("content") or "").strip()
    if not title and not description and not body:
        raise ValueError("record has no searchable text")
    digest_input = "\n".join((title, description, body)).encode("utf-8")
    content_hash = hashlib.sha256(digest_input).hexdigest()
    parts = urlsplit(identifier)
    host = parts.netloc.lower() if parts.scheme in ("http", "https") else source["canonical_host"].lower()
    return {
        "source_id": source_id,
        "source_record_id": identifier,
        "canonical_url": identifier,
        "source_host": host,
        "lane": lane,
        "title": title[:1000],
        "description": description[:4000],
        "body_text": body[:100000],
        "content_hash": content_hash,
        "license": source.get("license", ""),
        "attribution_required": bool(source.get("attribution_required", True)),
        "attribution_text": source.get("attribution_text", source.get("name", source_id)),
    }


def iter_records(path, fmt):
    if fmt == "jsonl":
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"line {line_number}: invalid JSON: {exc}") from exc
                    if not isinstance(value, dict):
                        raise ValueError(f"line {line_number}: expected JSON object")
                    yield value
    elif fmt == "csv":
        with path.open(encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle)
    else:
        raise ValueError("format must be jsonl or csv")


def run(source_path, input_path, output_path, fmt):
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source.get("review_status") != REQUIRED_REVIEW:
        raise ValueError("source is not approved: review_status must be reviewed_allowed")
    if source.get("source_status") not in ("proposed", "active"):
        raise ValueError("source is paused or retired")
    if source.get("lane") not in LANES:
        raise ValueError("source has an invalid lane")
    if not source.get("license") and not source.get("terms_url"):
        raise ValueError("source must document a license or terms URL")
    if not source.get("source_id") or not source.get("canonical_host"):
        raise ValueError("source_id and canonical_host are required")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    accepted = rejected = 0
    seen = set()
    with temp.open("w", encoding="utf-8") as out:
        for row_number, record in enumerate(iter_records(input_path, fmt), 1):
            try:
                normalized = normalize(record, source)
                key = normalized["source_record_id"]
                if key in seen:
                    rejected += 1
                    continue
                seen.add(key)
                out.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                accepted += 1
            except (ValueError, TypeError) as exc:
                print(f"reject row {row_number}: {exc}", file=sys.stderr)
                rejected += 1
    temp.replace(output_path)
    print(json.dumps({
        "source_id": source["source_id"],
        "accepted": accepted,
        "rejected_or_duplicate": rejected,
        "staged_file": str(output_path),
        "published_to_production": False
    }, indent=2))
    return accepted, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="reviewed source registry JSON")
    parser.add_argument("--input", required=True, type=Path, help="bulk JSONL or CSV file")
    parser.add_argument("--output", required=True, type=Path, help="staging JSONL destination")
    parser.add_argument("--format", choices=("jsonl", "csv"), required=True)
    args = parser.parse_args()
    run(args.source, args.input, args.output, args.format)


if __name__ == "__main__":
    main()
