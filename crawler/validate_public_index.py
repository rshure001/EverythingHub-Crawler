#!/usr/bin/env python3
"""Validate public search shards and the compressed catalog against the crawler index."""
from __future__ import annotations

import re
import zlib
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from lane_classifier import VALID_LANES
from index_store import load_index
from build_search_shards import query_tokens

ROOT = Path(__file__).resolve().parents[1]
MAX_SHARD_BYTES = 350_000
MAX_SHARD_UNCOMPRESSED_BYTES = 600_000
MAX_TOTAL_COMPRESSED_SHARD_BYTES = 150_000_000
MAX_TOTAL_LEGACY_SHARD_BYTES = 150_000_000
MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES = 540_000_000
MAX_SHARD_COUNT = 900
MAX_PAGES_PER_SHARD = 2_000
# Keep validation aligned with the independent Vercel backend's bounded decoder.
# The Supabase Edge Function uses a smaller 30 MB budget and delegates larger
# catalogs to Vercel rather than scanning all shards in its CPU-limited isolate.
MAX_CATALOG_GZIP_BYTES = 16_000_000
MAX_CATALOG_UNCOMPRESSED_BYTES = 64_000_000
# GitHub recommends keeping individual repository files at or below 50 MB.
# Stop before the 100 MB hard limit so a growing canonical index cannot make
# the crawler commit fail unexpectedly.
RECOMMENDED_CANONICAL_INDEX_BYTES = 50_000_000
MAX_CANONICAL_INDEX_BYTES = 90_000_000
CATALOG_NAME = "catalog-v1.json.gz"
QUERY_INDEX_DIR_NAME = "query-index"
MAX_QUERY_INDEX_SHARDS = 256
MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES = 2_000_000
MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES = 8_000_000
MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES = 32_000_000
MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES = 64_000_000


def bounded_gzip_decompress(raw: bytes, max_bytes: int = MAX_CATALOG_UNCOMPRESSED_BYTES) -> bytes:
    """Decompress one gzip member while enforcing an output cap during decoding."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise ValueError("maximum decompressed size must be a non-negative integer")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = decoder.decompress(raw, max_bytes + 1)
    if len(decoded) > max_bytes or decoder.unconsumed_tail:
        raise ValueError("decompressed catalog exceeds the configured safety limit")
    if not decoder.eof:
        raise ValueError("compressed catalog is incomplete or invalid")
    if decoder.unused_data:
        raise ValueError("compressed catalog contains trailing data or extra gzip members")
    return decoded


def is_safe_public_url(value: object) -> bool:
    """Accept only HTTP(S) source URLs without userinfo or malformed hosts."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def canonical_index_size_warning(root: Path = ROOT) -> str | None:
    """Warn when the canonical JSON approaches GitHub's recommended file size."""
    source_path = root / "data" / "index.json"
    if not source_path.is_file():
        return None
    size_bytes = source_path.stat().st_size
    if size_bytes <= RECOMMENDED_CANONICAL_INDEX_BYTES:
        return None
    return (
        f"data/index.json is {size_bytes:,} bytes; GitHub recommends keeping "
        f"individual files at or below {RECOMMENDED_CANONICAL_INDEX_BYTES:,} bytes."
    )


def validate_query_index(
    manifest: dict,
    search_dir: Path,
    shard_records: list[dict],
    page_shard_by_url: dict[str, int],
) -> list[str]:
    """Validate every query posting against the exact verified page-shard contents."""
    errors: list[str] = []
    query_dir = search_dir / QUERY_INDEX_DIR_NAME
    actual_files = (
        {path.relative_to(search_dir).as_posix() for path in query_dir.glob("*.json.gz")}
        if query_dir.is_dir() else set()
    )
    metadata = manifest.get("query_index")
    if metadata is None:
        if actual_files:
            errors.append("unlisted query-index files exist without manifest metadata")
        return errors
    if not isinstance(metadata, dict) or metadata.get("version") != 1:
        return ["query-index metadata schema is invalid"]

    entries = metadata.get("shards")
    if not isinstance(entries, list):
        return errors + ["query-index shards must be an array"]
    if metadata.get("shard_count") != len(entries):
        errors.append("query-index shard_count does not match its shard list")
    if len(entries) > MAX_QUERY_INDEX_SHARDS:
        errors.append("query index exceeds the fixed 256-shard safety limit")

    expected_postings: dict[str, dict[int, int]] = {}
    for page in shard_records:
        shard_index = page_shard_by_url.get(page.get("url"))
        if shard_index is None:
            continue
        for token in query_tokens(page):
            token_shards = expected_postings.setdefault(token, {})
            token_shards[shard_index] = token_shards.get(shard_index, 0) + 1
    declared_shard_count = len(manifest.get("shards", []))
    expected_shard_indexes = {
        shard_index for postings in expected_postings.values() for shard_index in postings
    }
    if any(
        not isinstance(shard_index, int) or isinstance(shard_index, bool)
        or shard_index < 0 or shard_index >= declared_shard_count
        for shard_index in expected_shard_indexes
    ):
        errors.append("query-index source references a page-shard ordinal outside the verified manifest")
    expected_prefixes = {
        hashlib.sha256(token.encode("utf-8")).hexdigest()[:2]
        for token in expected_postings
    }

    seen_files: set[str] = set()
    seen_prefixes: set[str] = set()
    total_compressed = 0
    total_uncompressed = 0
    token_count = 0
    posting_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("query-index contains a non-object shard entry")
            continue
        name = entry.get("file")
        match = re.fullmatch(r"query-index/([0-9a-f]{2})\.json\.gz", name) if isinstance(name, str) else None
        if not match:
            errors.append(f"unsafe query-index filename: {name!r}")
            continue
        prefix = match.group(1)
        if name in seen_files or prefix in seen_prefixes:
            errors.append(f"duplicate query-index shard: {name}")
            continue
        seen_files.add(name)
        seen_prefixes.add(prefix)
        path = search_dir / name
        if not path.is_file():
            errors.append(f"missing query-index shard: {name}")
            continue
        raw = path.read_bytes()
        if not 1 <= len(raw) <= MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES:
            errors.append(f"query-index shard exceeds its compressed size limit: {name}")
            continue
        if entry.get("bytes") != len(raw):
            errors.append(f"query-index byte-size mismatch: {name}")
            continue
        declared_uncompressed = entry.get("uncompressed_bytes")
        if (
            not isinstance(declared_uncompressed, int)
            or isinstance(declared_uncompressed, bool)
            or not 2 <= declared_uncompressed <= MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES
        ):
            errors.append(f"invalid query-index decoded byte count: {name}")
            continue
        digest = entry.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            errors.append(f"invalid query-index SHA-256: {name}")
            continue
        if hashlib.sha256(raw).hexdigest() != digest:
            errors.append(f"query-index SHA-256 mismatch: {name}")
            continue
        try:
            decoded = bounded_gzip_decompress(raw, MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES)
            payload = json.loads(decoded.decode("utf-8"))
        except Exception as exc:
            errors.append(f"query-index shard is invalid: {name}: {exc}")
            continue
        if len(decoded) != declared_uncompressed:
            errors.append(f"query-index decoded byte-size mismatch: {name}")
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("prefix") != prefix
            or not isinstance(tokens, dict)
            or len(tokens) != entry.get("items")
        ):
            errors.append(f"query-index shard schema or item count is invalid: {name}")
            continue

        expected_for_prefix = {
            token: [
                [shard_index, page_hits]
                for shard_index, page_hits in sorted(expected_postings[token].items())
            ]
            for token in sorted(expected_postings)
            if hashlib.sha256(token.encode("utf-8")).hexdigest()[:2] == prefix
        }
        if tokens != expected_for_prefix:
            errors.append(f"query-index postings do not match verified page shards: {name}")

        token_count += len(tokens)
        posting_count += sum(len(postings) for postings in tokens.values() if isinstance(postings, list))
        total_compressed += len(raw)
        total_uncompressed += len(decoded)
        if total_compressed > MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES:
            errors.append("query index exceeds the aggregate compressed safety limit")
        if total_uncompressed > MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES:
            errors.append("query index exceeds the aggregate decoded safety limit")

    if seen_files != actual_files:
        errors.append(
            f"query-index file set mismatch; unlisted={sorted(actual_files - seen_files)}, "
            f"missing={sorted(seen_files - actual_files)}"
        )
    if seen_prefixes != expected_prefixes:
        errors.append(
            f"query-index prefixes do not match expected tokens; "
            f"missing={sorted(expected_prefixes - seen_prefixes)}, "
            f"extra={sorted(seen_prefixes - expected_prefixes)}"
        )
    if metadata.get("token_count") != token_count or token_count != len(expected_postings):
        errors.append("query-index token count does not match verified postings")
    if metadata.get("posting_count") != posting_count:
        errors.append("query-index posting count does not match verified postings")
    if metadata.get("compressed_bytes") != total_compressed:
        errors.append("query-index compressed byte total does not match verified files")
    if metadata.get("uncompressed_bytes") != total_uncompressed:
        errors.append("query-index decoded byte total does not match verified files")
    return errors


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    source_path = root / "data" / "index.json"
    search_dir = root / "public-search"
    manifest_path = search_dir / "manifest.json"

    if not source_path.is_file():
        return ["data/index.json is missing"]
    if not manifest_path.is_file():
        return ["public-search/manifest.json is missing"]
    source_size = source_path.stat().st_size
    if source_size > MAX_CANONICAL_INDEX_BYTES:
        return [
            f"data/index.json is {source_size:,} bytes, above the "
            f"{MAX_CANONICAL_INDEX_BYTES:,}-byte safety ceiling; split or "
            "externalize the canonical index before committing another crawl."
        ]

    try:
        source = load_index(source_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"index or manifest is invalid JSON: {exc}"]

    # The watchdog uses this timestamp to judge search-index freshness. Reject a
    # missing, malformed, or stale manifest timestamp before any publisher can
    # treat the catalog as current.
    source_updated_at = source.get("updated_at") if isinstance(source, dict) else None
    manifest_updated_at = manifest.get("updated_at") if isinstance(manifest, dict) else None
    source_timestamp_valid = (
        isinstance(source_updated_at, int)
        and not isinstance(source_updated_at, bool)
        and source_updated_at > 0
    )
    manifest_timestamp_valid = (
        isinstance(manifest_updated_at, int)
        and not isinstance(manifest_updated_at, bool)
        and manifest_updated_at > 0
    )
    if not source_timestamp_valid:
        errors.append("canonical index updated_at must be a positive Unix timestamp")
    if not manifest_timestamp_valid:
        errors.append("manifest updated_at must be a positive Unix timestamp")
    elif source_timestamp_valid and manifest_updated_at != source_updated_at:
        errors.append("manifest updated_at does not match canonical index")

    if not isinstance(source.get("pages"), list):
        errors.append("data/index.json pages must be an array")
        source_pages = []
    else:
        source_pages = source["pages"]

    if manifest.get("version") != 1:
        errors.append("public search manifest version must be 1")
    integrity = manifest.get("integrity")
    if integrity not in (None, "sha256"):
        errors.append(f"unsupported public search integrity mode: {integrity!r}")
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        return errors + ["manifest shards must be an array"]
    if manifest.get("shard_count") != len(shards):
        errors.append("manifest shard_count does not match shards array")
    if len(shards) > MAX_SHARD_COUNT:
        errors.append("manifest exceeds the fixed 900-shard safety limit")

    expected_urls = {
        page.get("url")
        for page in source_pages
        if isinstance(page, dict)
        and page.get("robots_allowed") is not False
        and is_safe_public_url(page.get("url"))
    }

    seen_urls: set[str] = set()
    page_shard_by_url: dict[str, int] = {}
    declared_files: set[str] = set()
    shard_records: list[dict] = []
    total_pages = 0
    total_shard_bytes = 0
    total_uncompressed_bytes = 0
    shard_format = None

    for shard_index, entry in enumerate(shards):
        if not isinstance(entry, dict):
            errors.append("manifest contains a non-object shard entry")
            continue
        name = entry.get("file")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.startswith("shard-")
            or not (name.endswith(".json") or name.endswith(".json.gz"))
        ):
            errors.append(f"unsafe shard filename: {name!r}")
            continue
        compressed = name.endswith(".json.gz")
        current_format = "gzip" if compressed else "legacy"
        if shard_format is None:
            shard_format = current_format
        elif shard_format != current_format:
            errors.append("public search manifest mixes compressed and legacy raw shards")
            continue
        if name in declared_files:
            errors.append(f"duplicate shard filename: {name}")
            continue
        declared_files.add(name)
        declared_pages = entry.get("pages")
        if type(declared_pages) is not int or not 1 <= declared_pages <= MAX_PAGES_PER_SHARD:
            errors.append(f"shard page count exceeds the {MAX_PAGES_PER_SHARD:,}-page safety limit: {name}")
            continue
        path = search_dir / name
        if not path.is_file():
            errors.append(f"missing shard file: {name}")
            continue

        raw = path.read_bytes()
        if len(raw) > MAX_SHARD_BYTES:
            errors.append(f"shard exceeds {MAX_SHARD_BYTES} bytes: {name}")
        if entry.get("bytes") != len(raw):
            errors.append(f"byte-size mismatch: {name}")

        # Verify the compressed bytes before attempting decompression. This
        # rejects tampering deterministically and avoids parsing untrusted data.
        digest = entry.get("sha256")
        digest_valid = True
        if digest is None and integrity == "sha256":
            errors.append(f"missing SHA-256 digest: {name}")
            digest_valid = False
        elif digest is not None:
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                errors.append(f"invalid SHA-256 digest: {name}")
                digest_valid = False
            elif hashlib.sha256(raw).hexdigest() != digest:
                errors.append(f"SHA-256 mismatch: {name}")
                digest_valid = False
        if not digest_valid:
            continue

        if compressed:
            declared_uncompressed = entry.get("uncompressed_bytes")
            if (
                not isinstance(declared_uncompressed, int)
                or isinstance(declared_uncompressed, bool)
                or not 2 <= declared_uncompressed <= MAX_SHARD_UNCOMPRESSED_BYTES
            ):
                errors.append(f"invalid uncompressed shard byte count: {name}")
                continue
            try:
                decoded = bounded_gzip_decompress(raw, MAX_SHARD_UNCOMPRESSED_BYTES)
            except Exception as exc:
                errors.append(f"compressed shard is invalid: {name}: {exc}")
                continue
            if len(decoded) != declared_uncompressed:
                errors.append(f"uncompressed byte-size mismatch: {name}")
        else:
            declared_uncompressed = entry.get("uncompressed_bytes")
            if declared_uncompressed is not None and declared_uncompressed != len(raw):
                errors.append(f"uncompressed byte-size mismatch: {name}")
            decoded = raw

        total_shard_bytes += len(raw)
        total_uncompressed_bytes += len(decoded)
        if compressed and total_shard_bytes > MAX_TOTAL_COMPRESSED_SHARD_BYTES:
            errors.append("compressed public shards exceed the 100 MB aggregate safety limit")
        if not compressed and total_shard_bytes > MAX_TOTAL_LEGACY_SHARD_BYTES:
            errors.append("legacy raw public shards exceed the 100 MB aggregate safety limit")
        if total_uncompressed_bytes > MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES:
            errors.append("public shards exceed the 400 MB aggregate decompression safety limit")


        try:
            payload = json.loads(decoded.decode("utf-8"))
        except Exception as exc:
            errors.append(f"invalid JSON in {name}: {exc}")
            continue
        if not isinstance(payload, dict) or payload.get("version") != 1:
            errors.append(f"invalid shard schema: {name}")
            continue
        pages = payload.get("pages")
        if not isinstance(pages, list):
            errors.append(f"shard pages must be an array: {name}")
            continue
        if entry.get("pages") != len(pages):
            errors.append(f"page-count mismatch: {name}")

        total_pages += len(pages)
        for page in pages:
            if not isinstance(page, dict):
                errors.append(f"non-object page record in {name}")
                continue
            shard_records.append(page)
            url = page.get("url")
            if not isinstance(url, str):
                errors.append(f"missing URL in {name}")
                continue
            if not is_safe_public_url(url):
                errors.append(f"non-public-web URL or credential-bearing URL in {name}: {url[:160]}")
                continue
            lane = page.get("lane")
            if not isinstance(lane, str) or lane not in VALID_LANES:
                errors.append(f"missing or invalid search lane in {name}: {lane!r}")
            if url in seen_urls:
                errors.append(f"duplicate URL across public shards: {url[:160]}")
            else:
                seen_urls.add(url)
                page_shard_by_url[url] = shard_index

    if manifest.get("total_pages") != total_pages:
        errors.append("manifest total_pages does not match actual shard records")
    if total_pages != len(seen_urls):
        errors.append("public shard record count differs from unique URL count")
    if seen_urls != expected_urls:
        errors.append(
            f"public shards do not match canonical index; "
            f"missing={len(expected_urls - seen_urls)}, extra={len(seen_urls - expected_urls)}"
        )

    actual_files = {path.name for path in search_dir.glob("shard-*.json*")}
    if actual_files != declared_files:
        errors.append(
            f"shard file set mismatch; unlisted={sorted(actual_files - declared_files)}, "
            f"missing={sorted(declared_files - actual_files)}"
        )

    errors.extend(validate_query_index(
        manifest, search_dir, shard_records, page_shard_by_url
    ))

    catalog = manifest.get("catalog_gzip")
    # Older manifests remain valid and are served through the verified shard fallback.
    if catalog is not None:
        if not isinstance(catalog, dict):
            errors.append("manifest catalog_gzip metadata must be an object")
        elif catalog.get("file") != CATALOG_NAME:
            errors.append(f"catalog filename must be {CATALOG_NAME}")
        else:
            catalog_path = search_dir / CATALOG_NAME
            if not catalog_path.is_file():
                errors.append(f"missing compressed catalog: {CATALOG_NAME}")
            else:
                raw = catalog_path.read_bytes()
                if len(raw) > MAX_CATALOG_GZIP_BYTES:
                    errors.append(f"compressed catalog exceeds the {MAX_CATALOG_GZIP_BYTES:,}-byte safety limit")
                if catalog.get("bytes") != len(raw):
                    errors.append("compressed catalog byte-size mismatch")
                digest = catalog.get("sha256")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                ):
                    errors.append("compressed catalog SHA-256 is invalid")
                elif hashlib.sha256(raw).hexdigest() != digest:
                    errors.append("compressed catalog SHA-256 mismatch")
                try:
                    decoded = bounded_gzip_decompress(raw, MAX_CATALOG_UNCOMPRESSED_BYTES)
                    payload = json.loads(decoded.decode("utf-8"))
                    catalog_pages = payload.get("pages") if isinstance(payload, dict) else None
                    if not isinstance(catalog_pages, list):
                        errors.append("compressed catalog pages must be an array")
                    else:
                        if payload.get("version") != 1:
                            errors.append("compressed catalog version must be 1")
                        if len(catalog_pages) != total_pages:
                            errors.append("compressed catalog page count differs from the shards")
                        if catalog.get("pages") != len(catalog_pages):
                            errors.append("compressed catalog metadata page count mismatch")
                        if catalog_pages != shard_records:
                            errors.append("compressed catalog records differ from the verified shards")
                except Exception as exc:
                    errors.append(f"compressed catalog is invalid: {exc}")

    return errors


def validation_summary(manifest: dict) -> str:
    """Describe verified gzip transport or the verified shard-only fallback."""
    summary = (
        "Public search index valid: "
        f"{manifest['total_pages']:,} pages across {manifest['shard_count']:,} shards; "
        "all shard sizes, SHA-256 digests, URLs, and records match data/index.json; "
    )
    query_index = manifest.get("query_index")
    if isinstance(query_index, dict):
        summary += (
            f"query-scoped page index verified ({query_index['token_count']:,} tokens, "
            f"{query_index['posting_count']:,} token-to-shard postings); "
        )
    else:
        summary += "query-scoped page index omitted; "
    catalog = manifest.get("catalog_gzip")
    if isinstance(catalog, dict):
        return summary + f"compressed catalog verified ({catalog['bytes']:,} bytes)."
    return summary + "compressed catalog omitted; verified shard fallback remains available."


def main() -> None:
    errors = validate()
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        raise SystemExit(1)
    manifest = json.loads((ROOT / "public-search" / "manifest.json").read_text(encoding="utf-8"))
    warning = canonical_index_size_warning()
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(validation_summary(manifest))


if __name__ == "__main__":
    main()
