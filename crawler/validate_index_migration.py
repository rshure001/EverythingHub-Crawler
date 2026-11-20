#!/usr/bin/env python3
"""Round-trip the real repository index through bounded gzip shards without modifying it."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from index_store import load_index, save_index

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "index.json"


def pages_digest(pages: list[dict]) -> str:
    digest = hashlib.sha256()
    for page in sorted(pages, key=lambda row: row["url"]):
        raw = json.dumps(
            page,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def main() -> None:
    source_bytes = SOURCE.stat().st_size
    source = load_index(SOURCE)
    source_pages = source.get("pages")
    if not isinstance(source_pages, list):
        raise SystemExit("repository canonical index does not contain a pages array")
    expected_digest = pages_digest(source_pages)

    with tempfile.TemporaryDirectory(prefix="eh-index-migration-") as directory:
        target = Path(directory) / "index.json"
        manifest = save_index(target, source)
        restored = load_index(target)
        restored_pages = restored.get("pages")
        if not isinstance(restored_pages, list):
            raise SystemExit("sharded canonical index did not restore a pages array")
        actual_digest = pages_digest(restored_pages)
        if len(restored_pages) != len(source_pages) or actual_digest != expected_digest:
            raise SystemExit(
                "canonical index sharding round-trip changed page count or record content"
            )

        entries = manifest["shards"]
        compressed_bytes = sum(entry["bytes"] for entry in entries)
        uncompressed_bytes = sum(entry["uncompressed_bytes"] for entry in entries)
        max_compressed_shard = max((entry["bytes"] for entry in entries), default=0)
        max_uncompressed_shard = max((entry["uncompressed_bytes"] for entry in entries), default=0)
        manifest_bytes = target.stat().st_size
        if manifest_bytes >= 1_000_000:
            raise SystemExit("canonical index manifest is not below the 1 MB safety limit")
        if max_compressed_shard > 2_000_000 or max_uncompressed_shard > 4_000_000:
            raise SystemExit("canonical index shard exceeded its configured per-file bound")

        print("Canonical index sharded migration dry-run passed.")
        print(f"Pages preserved: {len(source_pages):,}")
        print(f"Record-content SHA-256: {expected_digest}")
        print(f"Legacy monolith bytes: {source_bytes:,}")
        print(f"New manifest bytes: {manifest_bytes:,}")
        print(f"Compressed shard bytes total: {compressed_bytes:,}")
        print(f"Uncompressed shard bytes total: {uncompressed_bytes:,}")
        print(f"Canonical shards: {len(entries):,}")
        print(f"Largest compressed shard: {max_compressed_shard:,} bytes")
        print(f"Largest uncompressed shard: {max_uncompressed_shard:,} bytes")
        print("Repository data was not modified; this validation used a temporary directory.")


if __name__ == "__main__":
    main()
