#!/usr/bin/env python3
"""Validate and stage the public search catalog for the static publisher."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import pathlib
import re
import shutil
import zlib
from urllib.parse import urlsplit
from typing import Any

MAX_INDEXED_PAGES = 125_000
MAX_SHARD_COUNT = 512
MAX_SHARD_BYTES = 350_000
MAX_SHARD_UNCOMPRESSED_BYTES = 500_000
MAX_TOTAL_COMPRESSED_SHARD_BYTES = 64_000_000
MAX_TOTAL_LEGACY_SHARD_BYTES = 100_000_000
MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES = 256_000_000
MAX_CATALOG_GZIP_BYTES = 16_000_000
MAX_CATALOG_UNCOMPRESSED_BYTES = 64_000_000
SHARD_NAME = re.compile(r"shard-\d{4}\.json(?:\.gz)?\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z", re.IGNORECASE)


def bounded_gunzip(raw: bytes, max_bytes: int) -> bytes:
    """Decode at most max_bytes + 1 so a gzip bomb cannot allocate unbounded memory."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            decoded = stream.read(max_bytes + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("gzip payload is invalid") from exc
    if len(decoded) > max_bytes:
        raise ValueError("gzip payload exceeds its decompressed safety limit")
    return decoded


def _validate_page(page: Any, shard_name: str) -> None:
    if not isinstance(page, dict):
        raise ValueError(f"{shard_name}: page record is not an object")
    url = page.get("url")
    if not isinstance(url, str):
        raise ValueError(f"{shard_name}: page URL is missing")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{shard_name}: page URL is unsafe") from exc
    if page.get("robots_allowed") is False or page.get("robotsAllowed") is False:
        raise ValueError(f"{shard_name}: robots-denied page is not publishable")


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("public search manifest version must be 1")
    if manifest.get("integrity") != "sha256":
        raise ValueError("public search manifest must declare SHA-256 integrity")
    updated_at = manifest.get("updated_at")
    total_pages = manifest.get("total_pages")
    shard_count = manifest.get("shard_count")
    shards = manifest.get("shards")
    if not isinstance(updated_at, int) or updated_at <= 0:
        raise ValueError("manifest updated_at must be a positive integer")
    if not isinstance(total_pages, int) or not 1 <= total_pages <= MAX_INDEXED_PAGES:
        raise ValueError("manifest total_pages exceeds the 125,000-page safety limit")
    if not isinstance(shard_count, int) or not 1 <= shard_count <= MAX_SHARD_COUNT:
        raise ValueError("manifest shard_count exceeds the 512-shard safety limit")
    if not isinstance(shards, list) or len(shards) != shard_count:
        raise ValueError("manifest shard_count does not match its shard list")

    names: set[str] = set()
    shard_format: str | None = None
    total_bytes = 0
    total_uncompressed = 0
    total_shard_pages = 0
    for shard in shards:
        if not isinstance(shard, dict):
            raise ValueError("public shard descriptor is not an object")
        name = shard.get("file")
        if not isinstance(name, str) or not SHARD_NAME.fullmatch(name) or name in names:
            raise ValueError(f"unsafe or duplicate shard filename: {name!r}")
        names.add(name)
        compressed = name.endswith(".json.gz")
        current_format = "gzip" if compressed else "legacy"
        if shard_format is not None and shard_format != current_format:
            raise ValueError("manifest mixes compressed and legacy raw shards")
        shard_format = current_format

        pages = shard.get("pages")
        byte_count = shard.get("bytes")
        digest = shard.get("sha256")
        if not isinstance(pages, int) or not 1 <= pages <= 1000:
            raise ValueError(f"{name}: shard page count is invalid")
        if not isinstance(byte_count, int) or not 1 <= byte_count <= MAX_SHARD_BYTES:
            raise ValueError(f"{name}: shard byte count exceeds the 350 KB file limit")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError(f"{name}: SHA-256 digest is invalid")

        uncompressed = shard.get("uncompressed_bytes")
        if compressed:
            if not isinstance(uncompressed, int) or not 1 <= uncompressed <= MAX_SHARD_UNCOMPRESSED_BYTES:
                raise ValueError(f"{name}: uncompressed byte count is invalid")
        else:
            if uncompressed is not None and uncompressed != byte_count:
                raise ValueError(f"{name}: legacy uncompressed byte count is inconsistent")
            uncompressed = byte_count

        total_bytes += byte_count
        total_uncompressed += uncompressed
        total_shard_pages += pages
        if compressed and total_bytes > MAX_TOTAL_COMPRESSED_SHARD_BYTES:
            raise ValueError("compressed shards exceed the 64 MB aggregate transfer limit")
        if not compressed and total_bytes > MAX_TOTAL_LEGACY_SHARD_BYTES:
            raise ValueError("legacy shards exceed the 100 MB transition limit")
        if total_uncompressed > MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES:
            raise ValueError("shards exceed the 256 MB decompressed safety limit")

    if total_shard_pages != total_pages:
        raise ValueError("shard page totals do not match manifest total_pages")

    catalog = manifest.get("catalog_gzip")
    if catalog is not None:
        if (
            not isinstance(catalog, dict)
            or catalog.get("file") != "catalog-v1.json.gz"
            or catalog.get("pages") != total_pages
            or not isinstance(catalog.get("bytes"), int)
            or not 1 <= catalog["bytes"] <= MAX_CATALOG_GZIP_BYTES
            or not isinstance(catalog.get("sha256"), str)
            or not SHA256.fullmatch(catalog["sha256"])
        ):
            raise ValueError("compressed catalog metadata is invalid")

    return {
        "shards": shards,
        "shard_format": shard_format,
        "total_bytes": total_bytes,
        "total_uncompressed_bytes": total_uncompressed,
        "total_pages": total_pages,
        "catalog_gzip": catalog,
    }


def decode_shard(shard: dict[str, Any], raw: bytes) -> list[dict[str, Any]]:
    """Verify the byte digest before any decompression or JSON parsing."""
    name = shard.get("file", "unknown shard")
    if not isinstance(raw, bytes) or len(raw) != shard.get("bytes"):
        raise ValueError(f"{name}: byte length mismatch")
    digest = hashlib.sha256(raw).hexdigest()
    if digest.lower() != str(shard.get("sha256", "")).lower():
        raise ValueError(f"{name}: SHA-256 mismatch")

    if name.endswith(".json.gz"):
        decoded = bounded_gunzip(raw, MAX_SHARD_UNCOMPRESSED_BYTES)
        if len(decoded) != shard.get("uncompressed_bytes"):
            raise ValueError(f"{name}: uncompressed byte length mismatch")
    else:
        decoded = raw
        if shard.get("uncompressed_bytes") not in (None, len(decoded)):
            raise ValueError(f"{name}: legacy uncompressed byte length mismatch")

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name}: shard JSON is invalid") from exc
    pages = payload.get("pages") if isinstance(payload, dict) and payload.get("version") == 1 else None
    if not isinstance(pages, list) or len(pages) != shard.get("pages"):
        raise ValueError(f"{name}: shard payload page count or schema mismatch")
    for page in pages:
        _validate_page(page, name)
    return pages


def stage_search_catalog(source_dir: pathlib.Path, target_dir: pathlib.Path) -> dict[str, Any]:
    source_dir = pathlib.Path(source_dir)
    target_dir = pathlib.Path(target_dir)
    manifest_path = source_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checked = validate_manifest(manifest)

    all_pages: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for shard in checked["shards"]:
        name = shard["file"]
        pages = decode_shard(shard, (source_dir / name).read_bytes())
        for page in pages:
            url = page["url"]
            if url in seen_urls:
                raise ValueError(f"{name}: duplicate URL across the catalog: {url}")
            seen_urls.add(url)
        all_pages.extend(pages)

    if len(all_pages) != checked["total_pages"]:
        raise ValueError("decoded catalog total does not match manifest total_pages")

    catalog = checked["catalog_gzip"]
    catalog_bytes = None
    if catalog is not None:
        catalog_raw = (source_dir / catalog["file"]).read_bytes()
        if len(catalog_raw) != catalog["bytes"]:
            raise ValueError("compressed catalog byte length mismatch")
        if hashlib.sha256(catalog_raw).hexdigest().lower() != catalog["sha256"].lower():
            raise ValueError("compressed catalog SHA-256 mismatch")
        catalog_payload = json.loads(bounded_gunzip(catalog_raw, MAX_CATALOG_UNCOMPRESSED_BYTES).decode("utf-8"))
        catalog_pages = catalog_payload.get("pages") if isinstance(catalog_payload, dict) and catalog_payload.get("version") == 1 else None
        if not isinstance(catalog_pages, list) or len(catalog_pages) != checked["total_pages"]:
            raise ValueError("compressed catalog page count or schema mismatch")
        if catalog_pages != all_pages:
            raise ValueError("compressed catalog records differ from the verified shards")
        catalog_bytes = len(catalog_raw)

    target_dir.mkdir(parents=True, exist_ok=True)
    for old in target_dir.glob("shard-*.json*"):
        old.unlink()
    stale_catalog = target_dir / "catalog-v1.json.gz"
    if stale_catalog.exists():
        stale_catalog.unlink()

    for shard in checked["shards"]:
        shutil.copyfile(source_dir / shard["file"], target_dir / shard["file"])
    if catalog is not None:
        shutil.copyfile(source_dir / catalog["file"], target_dir / catalog["file"])
    shutil.copyfile(manifest_path, target_dir / "manifest.json")

    return {
        "status": "validated-and-staged",
        "pages": checked["total_pages"],
        "shards": len(checked["shards"]),
        "unique_urls": len(seen_urls),
        "catalog_gzip_bytes": catalog_bytes,
        "search_transport": "gzip" if catalog is not None else "shards",
        "integrity": "sha256",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, default=pathlib.Path("public-search"))
    parser.add_argument("--target", type=pathlib.Path, default=pathlib.Path("site/search-index"))
    args = parser.parse_args()
    summary = stage_search_catalog(args.source, args.target)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
