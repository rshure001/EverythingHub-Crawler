#!/usr/bin/env python3
"""Create a machine-readable health record for the verified Everything Hub catalog."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CRAWLER_DIR = ROOT / "crawler"
if str(CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(CRAWLER_DIR))

from validate_media_catalog import validate_document


def build_health(
    manifest: dict[str, Any],
    generated_at: int | None = None,
    media_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    health = {
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

    if media_catalog is not None:
        media_errors, media_metrics = validate_document(media_catalog)
        if media_errors:
            raise ValueError("media catalog failed validation: " + media_errors[0])
        total_candidates = media_metrics["candidates"]
        health["media_discovery"] = {
            "schema": 1,
            "total_candidates": total_candidates,
            "by_type": media_metrics["by_type"],
            "media_origins": media_metrics["media_hosts"],
            "robots_allowed": media_metrics["robots_allowed"],
            "metadata_only_not_fetched": media_metrics["metadata_only_not_fetched"],
            "rights_unknown": media_metrics["unknown_rights"],
            "extraction_status": "not_performed_by_discovery_crawler",
        }

    return health


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("public-search/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("site/search-index/health.json"))
    parser.add_argument("--media-catalog", type=Path, default=Path("data/media-discovery.json"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    media_catalog = json.loads(args.media_catalog.read_text(encoding="utf-8"))
    health = build_health(manifest, media_catalog=media_catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "release-health-written",
        "total_pages": health["total_pages"],
        "shard_count": health["shard_count"],
        "source_updated_at": health["source_updated_at"],
        "media_candidates": health["media_discovery"]["total_candidates"],
        "media_types": health["media_discovery"]["by_type"],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
