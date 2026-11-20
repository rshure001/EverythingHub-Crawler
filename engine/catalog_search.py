"""Search the published Everything Hub catalog without a database."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zlib
from pathlib import Path
from typing import Any

from search_core import search

DEFAULT_SEARCH_DIR = Path(__file__).resolve().parents[1] / "public-search"
DEFAULT_CATALOG = DEFAULT_SEARCH_DIR / "catalog-v1.json.gz"
MAX_SHARD_BYTES = 350_000
MAX_SHARD_UNCOMPRESSED_BYTES = 500_000
MAX_SHARD_COUNT = 640
MAX_TOTAL_SHARD_BYTES = 100_000_000
MAX_TOTAL_COMPRESSED_SHARD_BYTES = 80_000_000
MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES = 320_000_000
MAX_INDEXED_PAGES = 150_000
MAX_CATALOG_GZIP_BYTES = 16_000_000
MAX_CATALOG_UNCOMPRESSED_BYTES = 64_000_000


def _bounded_gzip(raw: bytes, max_bytes: int = MAX_CATALOG_UNCOMPRESSED_BYTES) -> bytes:
    """Decompress one gzip member while enforcing an output cap."""
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = decoder.decompress(raw, max_bytes + 1)
    if len(decoded) > max_bytes or decoder.unconsumed_tail:
        raise ValueError("compressed catalog exceeds the decompression safety limit")
    if not decoder.eof:
        raise ValueError("compressed catalog is incomplete or invalid")
    if decoder.unused_data:
        raise ValueError("compressed catalog contains trailing data or extra gzip members")
    return decoded


def _load_gzip_catalog(
    path: str | Path,
    metadata: dict[str, Any] | None = None,
    expected_pages: int | None = None,
) -> list[dict[str, Any]]:
    catalog_path = Path(path)
    raw = catalog_path.read_bytes()
    if len(raw) > MAX_CATALOG_GZIP_BYTES:
        raise ValueError("compressed catalog exceeds the serving safety limit")
    if metadata is not None:
        if metadata.get("file") != "catalog-v1.json.gz":
            raise ValueError("compressed catalog filename is unsafe")
        if metadata.get("bytes") != len(raw):
            raise ValueError("compressed catalog byte count mismatch")
        if hashlib.sha256(raw).hexdigest() != metadata.get("sha256"):
            raise ValueError("compressed catalog SHA-256 mismatch")
    payload = json.loads(_bounded_gzip(raw).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("unsupported or malformed Everything Hub catalog")
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ValueError("catalog pages must be a list")
    if expected_pages is not None and len(pages) != expected_pages:
        raise ValueError("compressed catalog page count differs from the manifest")
    if len(pages) > MAX_INDEXED_PAGES:
        raise ValueError("compressed catalog exceeds the indexed-page safety limit")
    if metadata is not None and metadata.get("pages") != len(pages):
        raise ValueError("compressed catalog metadata page count mismatch")
    return [page for page in pages if isinstance(page, dict)]


def _load_shards(search_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    shards = manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != manifest.get("shard_count"):
        raise ValueError("public search manifest shard count is invalid")
    if len(shards) > MAX_SHARD_COUNT:
        raise ValueError("public search manifest exceeds the shard-count safety limit")
    if manifest.get("total_pages", 0) > MAX_INDEXED_PAGES:
        raise ValueError("public search manifest exceeds the indexed-page safety limit")

    declared_total_bytes = 0
    declared_uncompressed_bytes = 0
    shard_format = None
    for shard in shards:
        if not isinstance(shard, dict):
            raise ValueError("public search manifest contains a malformed shard entry")
        name = shard.get("file")
        if not isinstance(name, str) or not re.fullmatch(r"shard-\d{4}\.json(?:\.gz)?", name):
            raise ValueError(f"unsafe public search shard filename: {name!r}")
        compressed = name.endswith(".json.gz")
        current_format = "gzip" if compressed else "legacy"
        if shard_format is None:
            shard_format = current_format
        elif shard_format != current_format:
            raise ValueError("public search manifest mixes compressed and legacy raw shards")
        byte_count = shard.get("bytes")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or not 2 <= byte_count <= MAX_SHARD_BYTES:
            raise ValueError("public search manifest contains an invalid shard byte count")
        uncompressed_count = shard.get("uncompressed_bytes")
        if compressed:
            if (
                not isinstance(uncompressed_count, int)
                or isinstance(uncompressed_count, bool)
                or not 2 <= uncompressed_count <= MAX_SHARD_UNCOMPRESSED_BYTES
            ):
                raise ValueError(f"{name}: uncompressed shard byte count is invalid")
        else:
            if uncompressed_count is not None and uncompressed_count != byte_count:
                raise ValueError(f"{name}: legacy uncompressed byte count is invalid")
            uncompressed_count = byte_count
        declared_total_bytes += byte_count
        declared_uncompressed_bytes += uncompressed_count
        if compressed and declared_total_bytes > MAX_TOTAL_COMPRESSED_SHARD_BYTES:
            raise ValueError("compressed shard aggregate byte safety limit exceeded")
        if not compressed and declared_total_bytes > MAX_TOTAL_SHARD_BYTES:
            raise ValueError("aggregate byte safety limit exceeded")
        if declared_uncompressed_bytes > MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES:
            raise ValueError("aggregate shard decompression safety limit exceeded")

    pages: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for shard in shards:
        name = shard["file"]
        compressed = name.endswith(".json.gz")
        byte_count = shard["bytes"]
        page_count = shard.get("pages")
        digest = shard.get("sha256")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise ValueError(f"{name}: shard page count is invalid")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"{name}: shard SHA-256 is invalid")
        raw = (search_dir / name).read_bytes()
        if len(raw) != byte_count:
            raise ValueError(f"{name}: shard byte count mismatch")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"{name}: shard SHA-256 mismatch")
        decoded = _bounded_gzip(raw, MAX_SHARD_UNCOMPRESSED_BYTES) if compressed else raw
        expected_uncompressed = shard.get("uncompressed_bytes", byte_count)
        if len(decoded) != expected_uncompressed:
            raise ValueError(f"{name}: uncompressed shard byte count mismatch")
        payload = json.loads(decoded.decode("utf-8"))
        shard_pages = payload.get("pages") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(shard_pages, list) or len(shard_pages) != page_count:
            raise ValueError(f"{name}: shard payload does not match its manifest entry")
        for page in shard_pages:
            if not isinstance(page, dict) or not isinstance(page.get("url"), str):
                raise ValueError(f"{name}: shard contains a malformed page record")
            if page.get("robots_allowed") is False:
                raise ValueError(f"{name}: robots-denied page cannot enter public search")
            if page["url"] in seen_urls:
                raise ValueError(f"{name}: duplicate URL across public shards")
            seen_urls.add(page["url"])
            pages.append(page)

    if len(pages) != manifest.get("total_pages"):
        raise ValueError("public shard page count differs from the manifest")
    return pages


def load_catalog(
    path: str | Path | None = None,
    search_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load an explicit gzip catalog or the manifest-selected gzip/shard transport."""
    if path is not None:
        return _load_gzip_catalog(path)

    directory = Path(search_dir) if search_dir is not None else DEFAULT_SEARCH_DIR
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("unsupported or malformed public search manifest")
    if manifest.get("integrity") != "sha256":
        raise ValueError("public search manifest must declare SHA-256 integrity")
    updated_at = manifest.get("updated_at")
    if not isinstance(updated_at, int) or isinstance(updated_at, bool) or updated_at <= 0:
        raise ValueError("public search manifest timestamp is invalid")
    total_pages = manifest.get("total_pages")
    shard_count = manifest.get("shard_count")
    if not isinstance(total_pages, int) or isinstance(total_pages, bool) or total_pages < 1:
        raise ValueError("public search manifest total_pages is invalid")
    if not isinstance(shard_count, int) or isinstance(shard_count, bool) or shard_count < 1 or shard_count > MAX_SHARD_COUNT:
        raise ValueError("public search manifest shard_count is invalid")
    if total_pages > MAX_INDEXED_PAGES:
        raise ValueError("public search manifest total_pages exceeds the safety limit")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != shard_count:
        raise ValueError("public search manifest shard count is invalid")

    metadata = manifest.get("catalog_gzip")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ValueError("compressed catalog metadata is malformed")
        return _load_gzip_catalog(directory / "catalog-v1.json.gz", metadata, total_pages)
    return _load_shards(directory, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the local Everything Hub public catalog")
    parser.add_argument("query")
    parser.add_argument("--catalog", default=None, help="Optional explicit gzip catalog path")
    parser.add_argument("--lane", default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    pages = load_catalog(args.catalog)
    results = search(pages, args.query, lane=args.lane, limit=args.limit)
    print(json.dumps({"query": args.query, "catalog_pages": len(pages),
                      "result_count": len(results), "results": results},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
