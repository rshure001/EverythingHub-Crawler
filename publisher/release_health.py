#!/usr/bin/env python3
"""Create a machine-readable health record for the verified Everything Hub catalog."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


def build_health(manifest: dict[str, Any], generated_at: int | None = None) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("public search manifest version must be 1")
    if manifest.get("integrity") != "sha256":
        raise ValueError("public search manifest must declare SHA-256 integrity")

    updated_at = manifest.get("updated_at")
    total_pages = manifest.get("total_pages")
    shard_count = manifest.get("shard_count")
    shards = manifest.get("shards")
    catalog = manifest.get("catalog_gzip")

    if not isinstance(updated_at, int) or updated_at <= 0:
        raise ValueError("manifest updated_at must be a positive Unix timestamp")
    if not isinstance(total_pages, int) or total_pages < 1:
        raise ValueError("manifest total_pages must be a positive integer")
    if not isinstance(shard_count, int) or shard_count < 1:
        raise ValueError("manifest shard_count must be a positive integer")
    if not isinstance(shards, list) or len(shards) != shard_count:
        raise ValueError("manifest shard_count does not match its shard list")
    if not isinstance(catalog, dict) or catalog.get("file") != "catalog-v1.json.gz":
        raise ValueError("manifest compressed catalog metadata is missing or unsafe")
    if catalog.get("pages") != total_pages:
        raise ValueError("compressed catalog page count differs from the manifest")
    if not isinstance(catalog.get("bytes"), int) or catalog["bytes"] < 1:
        raise ValueError("compressed catalog byte count is invalid")
    digest = catalog.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("compressed catalog SHA-256 digest is invalid")

    timestamp = int(time.time()) if generated_at is None else generated_at
    if not isinstance(timestamp, int) or timestamp <= 0:
        raise ValueError("generated_at must be a positive Unix timestamp")

    return {
        "schema": 1,
        "source": "Everything Hub public-web crawler",
        "generated_at": timestamp,
        "source_updated_at": updated_at,
        "total_pages": total_pages,
        "shard_count": shard_count,
        "integrity": "sha256",
        "catalog_gzip": {
            "file": catalog["file"],
            "pages": catalog["pages"],
            "bytes": catalog["bytes"],
            "sha256": digest,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("public-search/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("site/search-index/health.json"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    health = build_health(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "release-health-written",
        "total_pages": health["total_pages"],
        "shard_count": health["shard_count"],
        "source_updated_at": health["source_updated_at"],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
