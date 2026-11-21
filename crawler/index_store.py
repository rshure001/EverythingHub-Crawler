#!/usr/bin/env python3
"""Bounded, integrity-checked canonical crawler index stored in stable gzip shards."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import zlib
from pathlib import Path
from typing import Any

FORMAT = "eh-index-shards-v1"
SHARD_DIR_NAME = "index-shards"
MAX_SHARD_COUNT = 256
MAX_SHARD_UNCOMPRESSED_BYTES = 4_000_000
MAX_SHARD_COMPRESSED_BYTES = 2_000_000
# Keep the aggregate below 640 MB (about 62.5% of the 256 x 4 MB shard envelope).
# This leaves headroom for the crawler's in-memory merge while avoiding a premature
# hard stop as the canonical corpus grows beyond the current 512 MB boundary.
MAX_TOTAL_UNCOMPRESSED_BYTES = 640_000_000
_BUCKET_RE = re.compile(r"^[0-9a-f]{2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def bucket_for_url(url: str) -> str:
    """Assign a URL to a stable bucket so new records do not renumber other shards."""
    if not isinstance(url, str) or not url:
        raise ValueError("canonical page URL must be a non-empty string")
    return hashlib.sha256(url.encode("utf-8")).digest()[:1].hex()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(raw)
    os.replace(temp, path)


def _bounded_decompress(raw: bytes, max_bytes: int) -> bytes:
    """Decompress one gzip member without allowing an oversized output buffer."""
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = decoder.decompress(raw, max_bytes + 1)
    if len(decoded) > max_bytes or decoder.unconsumed_tail:
        raise ValueError("canonical index shard exceeds its decompression limit")
    if not decoder.eof:
        raise ValueError("canonical index shard is incomplete or invalid")
    if decoder.unused_data:
        raise ValueError("canonical index shard contains trailing data or extra gzip members")
    return decoded


def load_index(index_path: str | Path, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load a legacy monolithic index or the versioned sharded canonical format."""
    path = Path(index_path)
    if not path.is_file():
        if fallback is not None:
            return fallback
        raise FileNotFoundError(path)

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"canonical index manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("canonical index must be a JSON object")

    storage_format = manifest.get("storage_format")
    if storage_format is None:
        # One-way migration compatibility: the next successful crawl rewrites
        # this legacy object into bounded shards without dropping its page data.
        if not isinstance(manifest.get("pages"), list):
            raise ValueError("legacy canonical index pages must be an array")
        return manifest
    if storage_format != FORMAT:
        raise ValueError(f"unsupported canonical index storage format: {storage_format!r}")

    shards = manifest.get("shards")
    shard_count = manifest.get("shard_count")
    total_pages = manifest.get("total_pages")
    if not isinstance(shards, list):
        raise ValueError("canonical index shards must be an array")
    if not isinstance(shard_count, int) or isinstance(shard_count, bool) or shard_count != len(shards):
        raise ValueError("canonical index shard_count does not match its shard list")
    if not isinstance(total_pages, int) or isinstance(total_pages, bool) or total_pages < 0:
        raise ValueError("canonical index total_pages must be a non-negative integer")
    if shard_count > MAX_SHARD_COUNT:
        raise ValueError("canonical index exceeds the fixed shard-count limit")

    shard_dir = path.parent / SHARD_DIR_NAME
    pages: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_buckets: set[str] = set()
    total_uncompressed = 0

    for entry in shards:
        if not isinstance(entry, dict):
            raise ValueError("canonical index contains a malformed shard entry")
        bucket = entry.get("bucket")
        name = entry.get("file")
        digest = entry.get("sha256")
        if not isinstance(bucket, str) or not _BUCKET_RE.fullmatch(bucket):
            raise ValueError(f"canonical index contains an invalid bucket: {bucket!r}")
        if not isinstance(digest, str) or not _SHA_RE.fullmatch(digest):
            raise ValueError(f"canonical index contains an invalid shard digest: {name!r}")
        expected_name = f"index-{bucket}-{digest}.json.gz"
        if name != expected_name or bucket in seen_buckets:
            raise ValueError(f"canonical index contains an unsafe or duplicate shard: {name!r}")
        seen_buckets.add(bucket)

        declared_bytes = entry.get("bytes")
        declared_uncompressed = entry.get("uncompressed_bytes")
        page_count = entry.get("pages")
        if (
            not isinstance(declared_bytes, int) or isinstance(declared_bytes, bool)
            or not 2 <= declared_bytes <= MAX_SHARD_COMPRESSED_BYTES
        ):
            raise ValueError(f"{name}: compressed byte count is invalid")
        if (
            not isinstance(declared_uncompressed, int) or isinstance(declared_uncompressed, bool)
            or not 2 <= declared_uncompressed <= MAX_SHARD_UNCOMPRESSED_BYTES
        ):
            raise ValueError(f"{name}: uncompressed byte count is invalid")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise ValueError(f"{name}: page count is invalid")
        shard_path = shard_dir / name
        try:
            raw = shard_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"{name}: canonical shard is missing or unreadable") from exc
        if len(raw) != declared_bytes:
            raise ValueError(f"{name}: compressed byte count mismatch")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"{name}: SHA-256 mismatch")

        decoded = _bounded_decompress(raw, MAX_SHARD_UNCOMPRESSED_BYTES)
        if len(decoded) != declared_uncompressed:
            raise ValueError(f"{name}: uncompressed byte count mismatch")
        total_uncompressed += len(decoded)
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("canonical index exceeds the aggregate decompression limit")

        try:
            payload = json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{name}: shard JSON is invalid") from exc
        shard_pages = payload.get("pages") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(shard_pages, list)
            or len(shard_pages) != page_count
        ):
            raise ValueError(f"{name}: shard schema or page count mismatch")

        for page in shard_pages:
            if not isinstance(page, dict) or not isinstance(page.get("url"), str):
                raise ValueError(f"{name}: shard contains a malformed page record")
            url = page["url"]
            if bucket_for_url(url) != bucket:
                raise ValueError(f"{name}: page is stored in the wrong stable bucket")
            if url in seen_urls:
                raise ValueError(f"{name}: duplicate canonical URL")
            seen_urls.add(url)
            pages.append(page)

    if len(pages) != total_pages:
        raise ValueError("canonical index total_pages does not match its shard records")

    result = {
        key: value for key, value in manifest.items()
        if key not in {"storage_format", "shard_count", "shards", "total_pages"}
    }
    result["pages"] = pages
    return result


def save_index(index_path: str | Path, index: dict[str, Any]) -> dict[str, Any]:
    """Write stable compressed URL buckets, then atomically replace the small manifest."""
    if not isinstance(index, dict) or not isinstance(index.get("pages"), list):
        raise ValueError("canonical index payload must contain a pages array")

    path = Path(index_path)
    shard_dir = path.parent / SHARD_DIR_NAME
    shard_dir.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict[str, Any]]] = {}
    seen_urls: set[str] = set()
    for page in index["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("url"), str):
            raise ValueError("canonical index contains a malformed page record")
        url = page["url"]
        if url in seen_urls:
            raise ValueError("canonical index contains duplicate URLs")
        seen_urls.add(url)
        buckets.setdefault(bucket_for_url(url), []).append(page)

    entries: list[dict[str, Any]] = []
    total_uncompressed = 0
    for bucket in sorted(buckets):
        bucket_pages = sorted(buckets[bucket], key=lambda page: page["url"])
        payload = json.dumps(
            {"version": 1, "pages": bucket_pages},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        uncompressed = payload.encode("utf-8")
        if len(uncompressed) > MAX_SHARD_UNCOMPRESSED_BYTES:
            raise ValueError(f"canonical bucket {bucket} exceeds the uncompressed shard limit")
        total_uncompressed += len(uncompressed)
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("canonical index exceeds the aggregate uncompressed limit")
        raw = gzip.compress(uncompressed, compresslevel=9, mtime=0)
        if len(raw) > MAX_SHARD_COMPRESSED_BYTES:
            raise ValueError(f"canonical bucket {bucket} exceeds the compressed shard limit")

        digest = hashlib.sha256(raw).hexdigest()
        name = f"index-{bucket}-{digest}.json.gz"
        shard_path = shard_dir / name
        if not shard_path.is_file() or shard_path.read_bytes() != raw:
            _atomic_write(shard_path, raw)
        entries.append({
            "bucket": bucket,
            "file": name,
            "pages": len(bucket_pages),
            "bytes": len(raw),
            "uncompressed_bytes": len(uncompressed),
            "sha256": digest,
        })

    metadata = {
        key: value for key, value in index.items()
        if key not in {"pages", "storage_format", "shard_count", "shards", "total_pages"}
    }
    manifest = {
        **metadata,
        "version": 1,
        "storage_format": FORMAT,
        "total_pages": len(index["pages"]),
        "shard_count": len(entries),
        "shards": entries,
    }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(manifest_raw) > 1_000_000:
        raise ValueError("canonical index manifest exceeds the 1 MB safety limit")

    # The manifest is replaced only after every referenced shard is durable.
    _atomic_write(path, manifest_raw)

    referenced = {entry["file"] for entry in entries}
    for stale in shard_dir.glob("index-*.json.gz"):
        if stale.name not in referenced:
            stale.unlink()

    return manifest
