#!/usr/bin/env python3
"""Reproducible synthetic benchmark for the staging importer; never publishes data."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from bulk_ingest import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=25000)
    parser.add_argument("--chunk-size", type=int, default=5000)
    args = parser.parse_args()
    if args.records < 1:
        parser.error("--records must be positive")

    source = {
        "source_id": "benchmark.synthetic",
        "name": "Synthetic benchmark fixture",
        "canonical_host": "benchmark.invalid",
        "lane": "general",
        "review_status": "reviewed_allowed",
        "source_status": "active",
        "license": "CC0-1.0 synthetic test data",
        "attribution_required": False,
    }
    with tempfile.TemporaryDirectory(prefix="eh-benchmark-") as directory:
        root = Path(directory)
        source_path = root / "source.json"
        input_path = root / "records.jsonl"
        output_path = root / "staged.jsonl"
        checkpoint_path = root / "checkpoint.sqlite3"
        source_path.write_text(json.dumps(source), encoding="utf-8")
        with input_path.open("w", encoding="utf-8") as handle:
            for number in range(args.records):
                handle.write(json.dumps({
                    "id": f"synthetic-{number:09d}",
                    "title": f"Benchmark record {number}",
                    "description": "Synthetic content used only to measure importer throughput",
                    "text": f"Record {number} contains searchable benchmark text.",
                }) + "\\n")

        started = time.perf_counter()
        accepted, rejected = run(
            source_path, input_path, output_path, "jsonl",
            checkpoint_path, args.chunk_size
        )
        elapsed = time.perf_counter() - started
        output_bytes = output_path.stat().st_size
        print(json.dumps({
            "benchmark": "synthetic_only",
            "input_records": args.records,
            "accepted_staged": accepted,
            "rejected_or_duplicate": rejected,
            "elapsed_seconds": round(elapsed, 3),
            "records_per_second": round(accepted / elapsed, 1) if elapsed else None,
            "staged_megabytes": round(output_bytes / (1024 * 1024), 3),
            "published_to_production": False,
        }, indent=2))


if __name__ == "__main__":
    main()
