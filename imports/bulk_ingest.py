#!/usr/bin/env python3
"""Safe, restartable staging importer for approved bulk JSONL/CSV datasets.

This tool writes normalized JSONL staging records only. It does not publish to production.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
import tempfile
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

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
    explicit_url = str(record.get("canonical_url") or record.get("url") or "").strip()
    parts = urlsplit(explicit_url)
    if parts.scheme in ("http", "https") and parts.netloc:
        canonical_url = explicit_url
        host = parts.netloc.lower()
    else:
        # Stable source-scoped URN: never mislabel a dataset's numeric/string ID as a URL.
        canonical_url = "urn:everythinghub:source:" + quote(source_id, safe="._-") + ":" + quote(identifier, safe="")
        host = source["canonical_host"].lower()
    return {
        "source_id": source_id,
        "source_record_id": identifier,
        "canonical_url": canonical_url,
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


def file_fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(source_path, input_path, output_path, fmt, checkpoint_path=None, chunk_size=5000):
    source = json.loads(source_path.read_text(encoding="utf-8"))
    input_fingerprint = file_fingerprint(input_path)
    source_fingerprint = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
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
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_path or output_path.with_suffix(output_path.suffix + ".checkpoint.sqlite3")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(checkpoint_path))
    db.execute("CREATE TABLE IF NOT EXISTS seen_ids (record_id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS import_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    state = dict(db.execute("SELECT key, value FROM import_state"))
    next_row = int(state.get("next_row", "0"))
    committed_bytes = int(state.get("output_bytes", "0"))
    if state and (state.get("input_fingerprint") != input_fingerprint or
                  state.get("source_fingerprint") != source_fingerprint):
        db.close()
        raise ValueError("checkpoint input/source fingerprint mismatch; use a fresh checkpoint")
    accepted_total = int(state.get("accepted", "0"))
    rejected_total = int(state.get("rejected", "0"))

    # Recover safely if a process stopped after appending a chunk but before checkpoint commit.
    if output_path.exists():
        with output_path.open("r+b") as out:
            out.truncate(committed_bytes)
    elif committed_bytes:
        raise ValueError("checkpoint refers to a missing staged output file")
    else:
        output_path.touch()

    accepted_chunk = rejected_chunk = 0
    chunk_rows = 0
    row_number = 0
    try:
        with output_path.open("a", encoding="utf-8") as out:
            for row_number, record in enumerate(iter_records(input_path, fmt), 1):
                if row_number <= next_row:
                    continue
                try:
                    normalized = normalize(record, source)
                    key = normalized["source_record_id"]
                    cursor = db.execute(
                        "INSERT OR IGNORE INTO seen_ids(record_id) VALUES (?)", (key,)
                    )
                    if cursor.rowcount == 0:
                        rejected_chunk += 1
                    else:
                        out.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                        accepted_chunk += 1
                except (ValueError, TypeError) as exc:
                    print(f"reject row {row_number}: {exc}", file=sys.stderr)
                    rejected_chunk += 1
                chunk_rows += 1
                if chunk_rows >= chunk_size:
                    out.flush()
                    new_bytes = out.tell()
                    accepted_total += accepted_chunk
                    rejected_total += rejected_chunk
                    next_row = row_number
                    db.execute("INSERT OR REPLACE INTO import_state VALUES ('input_fingerprint', ?)", (input_fingerprint,))
                    db.execute("INSERT OR REPLACE INTO import_state VALUES ('source_fingerprint', ?)", (source_fingerprint,))
                    db.execute("INSERT OR REPLACE INTO import_state VALUES ('input_fingerprint', ?)", (input_fingerprint,))
                db.execute("INSERT OR REPLACE INTO import_state VALUES ('source_fingerprint', ?)", (source_fingerprint,))
                db.execute("INSERT OR REPLACE INTO import_state VALUES ('next_row', ?)", (str(next_row),))
                    db.execute("INSERT OR REPLACE INTO import_state VALUES ('output_bytes', ?)", (str(new_bytes),))
                    db.execute("INSERT OR REPLACE INTO import_state VALUES ('accepted', ?)", (str(accepted_total),))
                    db.execute("INSERT OR REPLACE INTO import_state VALUES ('rejected', ?)", (str(rejected_total),))
                    db.commit()
                    accepted_chunk = rejected_chunk = chunk_rows = 0

            if chunk_rows:
                out.flush()
                new_bytes = out.tell()
                accepted_total += accepted_chunk
                rejected_total += rejected_chunk
                next_row = row_number
                db.execute("INSERT OR REPLACE INTO import_state VALUES ('next_row', ?)", (str(next_row),))
                db.execute("INSERT OR REPLACE INTO import_state VALUES ('output_bytes', ?)", (str(new_bytes),))
                db.execute("INSERT OR REPLACE INTO import_state VALUES ('accepted', ?)", (str(accepted_total),))
                db.execute("INSERT OR REPLACE INTO import_state VALUES ('rejected', ?)", (str(rejected_total),))
                db.commit()
    finally:
        db.close()

    print(json.dumps({
        "source_id": source["source_id"],
        "accepted": accepted_total,
        "rejected_or_duplicate": rejected_total,
        "rows_committed": next_row,
        "checkpoint_file": str(checkpoint_path),
        "staged_file": str(output_path),
        "published_to_production": False
    }, indent=2))
    return accepted_total, rejected_total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="reviewed source registry JSON")
    parser.add_argument("--input", required=True, type=Path, help="bulk JSONL or CSV file")
    parser.add_argument("--output", required=True, type=Path, help="staging JSONL destination")
    parser.add_argument("--format", choices=("jsonl", "csv"), required=True)
    parser.add_argument("--checkpoint", type=Path, help="persistent SQLite resume checkpoint")
    parser.add_argument("--chunk-size", type=int, default=5000, help="rows per committed checkpoint")
    args = parser.parse_args()
    run(args.source, args.input, args.output, args.format, args.checkpoint, args.chunk_size)


if __name__ == "__main__":
    main()
