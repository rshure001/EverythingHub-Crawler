#!/usr/bin/env python3
"""Build integrity-pinned public search shards and an optional compressed catalog."""
import gzip
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from lane_classifier import VALID_LANES, classify_lane
from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "index.json"
OUT = ROOT / "public-search"
MAX_BYTES = 600_000  # Decoded shard target; gzip transport must remain below 350 KB.
MAX_COMPRESSED_BYTES = 350_000
MAX_SHARD_COUNT = 900  # Keeps page shards plus metadata below GitHub's 1,000-entry directory API cap.
MAX_PAGES_PER_SHARD = 2_000
SHARD_GZIP_LEVEL = 6
# The independent Vercel backend safely decompresses up to 64 MB. The
# Supabase Edge Function has a smaller 30 MB/6 MB budget and delegates larger
# catalogs to Vercel rather than scanning all shards in its CPU-limited isolate.
MAX_CATALOG_GZIP_BYTES = 16_000_000
MAX_CATALOG_UNCOMPRESSED_BYTES = 64_000_000
CATALOG_NAME = "catalog-v1.json.gz"
QUERY_INDEX_DIR_NAME = "query-index"
MAX_QUERY_INDEX_SHARDS = 256
MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES = 2_000_000
MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES = 8_000_000
MAX_QUERY_INDEX_TOTAL_COMPRESSED_BYTES = 32_000_000
MAX_QUERY_INDEX_TOTAL_UNCOMPRESSED_BYTES = 96_000_000
QUERY_STOP_TOKENS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "our", "the", "to",
    "with", "your", "you", "we", "this", "that", "these", "those",
}


def is_publishable(doc):
    """Exclude robots-denied, malformed, and credential-bearing URLs from public search."""
    if not isinstance(doc, dict) or doc.get("robots_allowed") is False:
        return False
    url = doc.get("url")
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def compact(doc):
    url = str(doc.get("url", ""))[:2048]
    lane = doc.get("lane")
    classified_lane = classify_lane(url)
    if (
        not isinstance(lane, str)
        or lane not in VALID_LANES
        or (lane == "general" and classified_lane != "general")
    ):
        # Reclassify legacy general-lane records when a source host receives
        # an explicit category mapping. Preserve any existing non-general lane.
        lane = classified_lane
    compact_page = {
        "url": url,
        "title": str(doc.get("title", ""))[:500],
        "description": str(doc.get("description", ""))[:500],
        "text": str(doc.get("text", ""))[:1600],
        "lane": lane,
    }
    product = doc.get("product")
    if isinstance(product, dict) and product.get("type") == "product" and product.get("name"):
        compact_product = {"type": "product"}
        for key, limit in {
            "name": 500, "brand": 160, "sku": 120, "price": 64,
            "currency": 8, "availability": 80, "image": 2048, "url": 2048,
        }.items():
            value = product.get(key)
            if not isinstance(value, (str, int, float)) or not str(value).strip():
                continue
            normalized = str(value).strip()[:limit]
            if key in ("image", "url"):
                try:
                    parsed_value = urlsplit(normalized)
                    if (
                        parsed_value.scheme not in ("http", "https")
                        or not parsed_value.hostname
                        or parsed_value.username is not None
                        or parsed_value.password is not None
                    ):
                        continue
                except ValueError:
                    continue
            compact_product[key] = normalized
        compact_page["product"] = compact_product
    # Preserve crawl timestamps for freshness scoring; omit malformed values.
    fetched_at = doc.get("fetched_at")
    if isinstance(fetched_at, (int, float)) and fetched_at > 0:
        compact_page["fetched_at"] = int(fetched_at)
    elif isinstance(fetched_at, str) and fetched_at.strip():
        compact_page["fetched_at"] = fetched_at.strip()[:64]
    published_at = doc.get("published_at")
    if isinstance(published_at, (int, float)) and published_at > 0:
        compact_page["published_at"] = int(published_at)
    elif isinstance(published_at, str) and published_at.strip():
        compact_page["published_at"] = published_at.strip()[:64]
    return compact_page


def encode_batch(items):
    """Serialize exactly the bytes written to disk, including the final newline."""
    payload = json.dumps(
        {"version": 1, "pages": items},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    return payload.encode("utf-8")


def batch_fits(items, max_bytes=MAX_BYTES):
    """Measure exact uncompressed JSON bytes before deterministic gzip encoding."""
    return len(encode_batch(items)) <= max_bytes


def encoded_page_size(page):
    """Return exact UTF-8 JSON bytes for one compact page, without array framing."""
    return len(json.dumps(
        page, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8"))


def batch_size_from_item_sizes(item_sizes):
    """Calculate exact encode_batch size from page sizes in linear time."""
    if not item_sizes:
        return len(b'{"version":1,"pages":[]}') + 1
    return (
        len(b'{"version":1,"pages":[')
        + sum(item_sizes)
        + len(item_sizes) - 1
        + len(b']}')
        + 1
    )

def encode_shard(items):
    """Return deterministic gzip bytes and the exact decoded JSON length."""
    uncompressed = encode_batch(items)
    if len(uncompressed) > MAX_BYTES:
        raise ValueError("uncompressed public shard exceeds its size limit")
    compressed = gzip.compress(uncompressed, compresslevel=SHARD_GZIP_LEVEL, mtime=0)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ValueError("compressed public shard exceeds its size limit")
    return compressed, len(uncompressed)


def catalog_metadata(raw, page_count, uncompressed_bytes=None):
    """Return gzip metadata only when both compressed and decoded sizes are safe."""
    if uncompressed_bytes is not None and uncompressed_bytes > MAX_CATALOG_UNCOMPRESSED_BYTES:
        return None
    if len(raw) > MAX_CATALOG_GZIP_BYTES:
        return None
    return {
        "file": CATALOG_NAME,
        "pages": page_count,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def query_tokens(page):
    """Return conservative searchable tokens used to route queries to page shards."""
    values = [page.get("title"), page.get("url"), page.get("description"), page.get("text")]
    product = page.get("product")
    if isinstance(product, dict):
        values.extend(product.get(key) for key in ("name", "brand", "sku", "price", "availability"))
    tokens = set()
    for value in values:
        if not isinstance(value, (str, int, float)):
            continue
        # Keep parity with the JavaScript consumer: String.normalize("NFKC").toLowerCase().
        normalized = unicodedata.normalize("NFKC", str(value)).lower()
        tokens.update(
            token for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
            if token and token not in QUERY_STOP_TOKENS
        )
    return tokens


def build_query_index(token_shards):
    """Build deterministic token-to-page-shard postings with per-shard page counts."""
    shards = {}
    for token, shard_counts in sorted(token_shards.items()):
        prefix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:2]
        shard = shards.setdefault(prefix, {"version": 1, "prefix": prefix, "tokens": {}})
        shard["tokens"][token] = [
            [int(shard_index), int(count)]
            for shard_index, count in sorted(shard_counts.items())
            if isinstance(shard_index, int) and not isinstance(shard_index, bool)
            and isinstance(count, int) and not isinstance(count, bool) and count > 0
        ]
    return shards


def write_query_index(output_dir, token_shards):
    """Write bounded, deterministic query-index shards or omit the optional index safely."""
    query_dir = output_dir / QUERY_INDEX_DIR_NAME
    query_dir.mkdir(parents=True, exist_ok=True)
    payloads = build_query_index(token_shards)
    encoded = []
    total_compressed = 0
    total_uncompressed = 0
    posting_count = 0

    for prefix, payload in sorted(payloads.items()):
        raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        raw = raw_json.encode("utf-8")
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        if len(raw) > MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES:
            reason = f"query-index shard {prefix} exceeds the {MAX_QUERY_INDEX_SHARD_UNCOMPRESSED_BYTES:,}-byte decoded limit"
            for stale in query_dir.glob("*.json.gz"):
                stale.unlink()
            return None, reason
        if len(compressed) > MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES:
            reason = f"query-index shard {prefix} exceeds the {MAX_QUERY_INDEX_SHARD_COMPRESSED_BYTES:,}-byte compressed limit"
            for stale in query_dir.glob("*.json.gz"):
                stale.unlink()
            return None, reason
        total_compressed += len(compressed)
        total_uncompressed += len(raw)
        posting_count += sum(len(postings) for postings in payload["tokens"].values())
        encoded.append((prefix, payload, raw, compressed))

    if len(encoded) > MAX_QUERY_INDEX_SHARDS:
        reason = f"query index requires {len(encoded)} shards; limit is {MAX_QUERY_INDEX_SHARDS}"
        for stale in query_dir.glob("*.json.gz"):
            stale.unlink()
        return None, reason
    # Publish independently bounded prefix shards even when their aggregate is
    # larger than a single Edge Function response budget. Search consumers fetch
    # only the SHA-256 prefix shards needed for the user's query.
    expected_files = set()
    files = []
    for prefix, payload, raw, compressed in encoded:
        relative = f"{QUERY_INDEX_DIR_NAME}/{prefix}.json.gz"
        expected_files.add(f"{prefix}.json.gz")
        (output_dir / relative).write_bytes(compressed)
        files.append({
            "file": relative,
            "bytes": len(compressed),
            "uncompressed_bytes": len(raw),
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "items": len(payload["tokens"]),
        })
    for stale in query_dir.glob("*.json.gz"):
        if stale.name not in expected_files:
            stale.unlink()

    return {
        "version": 1,
        "shard_count": len(files),
        "token_count": sum(len(payload["tokens"]) for _prefix, payload, _raw, _compressed in encoded),
        "posting_count": posting_count,
        "compressed_bytes": total_compressed,
        "uncompressed_bytes": total_uncompressed,
        "shards": files,
    }, None


def main():
    source = load_index(SOURCE)
    pages = [compact(p) for p in source.get("pages", []) if is_publishable(p)]
    pages.sort(key=lambda p: (p["title"].casefold(), p["url"]))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("shard-*.json*"):
        old.unlink()

    shards = []
    query_postings = defaultdict(lambda: defaultdict(int))
    batch = []
    batch_payload_size = 0
    number = 0

    def write_batch(items):
        nonlocal number
        if not items:
            return
        if len(items) > MAX_PAGES_PER_SHARD:
            raise ValueError(f"public search shard exceeds the fixed {MAX_PAGES_PER_SHARD}-page limit")
        number += 1
        if number > MAX_SHARD_COUNT:
            raise ValueError(f"public search catalog exceeds the fixed {MAX_SHARD_COUNT}-shard limit")
        name = f"shard-{number:04d}.json.gz"
        raw, uncompressed_bytes = encode_shard(items)
        (OUT / name).write_bytes(raw)
        shards.append({
            "file": name,
            "pages": len(items),
            "bytes": len(raw),
            "uncompressed_bytes": uncompressed_bytes,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
        shard_index = len(shards) - 1
        for page in items:
            for token in query_tokens(page):
                query_postings[token][shard_index] += 1

    prefix_bytes = len(b'{"version":1,"pages":[')
    suffix_bytes = len(b']}\n')
    for page in pages:
        page_size = encoded_page_size(page)
        candidate_size = (
            prefix_bytes + batch_payload_size
            + (1 if batch else 0) + page_size + suffix_bytes
        )
        if batch and (candidate_size > MAX_BYTES or len(batch) >= MAX_PAGES_PER_SHARD):
            write_batch(batch)
            batch = []
            batch_payload_size = 0
            candidate_size = prefix_bytes + page_size + suffix_bytes
        if candidate_size > MAX_BYTES:
            raise ValueError("single compacted page exceeds the public shard size limit")
        batch.append(page)
        batch_payload_size += page_size + (1 if len(batch) > 1 else 0)
    write_batch(batch)
    query_index, query_index_omission_reason = write_query_index(OUT, query_postings)

    catalog_payload = json.dumps(
        {"version": 1, "pages": pages},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    catalog_payload_bytes = catalog_payload.encode("utf-8")
    catalog_raw = None
    catalog_gzip = None
    catalog_omission_reason = None
    if len(catalog_payload_bytes) > MAX_CATALOG_UNCOMPRESSED_BYTES:
        catalog_omission_reason = (
            f"{len(catalog_payload_bytes):,} uncompressed bytes exceeds "
            f"the {MAX_CATALOG_UNCOMPRESSED_BYTES:,}-byte limit"
        )
    else:
        catalog_raw = gzip.compress(catalog_payload_bytes, compresslevel=9, mtime=0)
        catalog_gzip = catalog_metadata(catalog_raw, len(pages), len(catalog_payload_bytes))
        if catalog_gzip is None:
            catalog_omission_reason = (
                f"{len(catalog_raw):,} compressed bytes exceeds "
                f"the {MAX_CATALOG_GZIP_BYTES:,}-byte limit"
            )
    catalog_path = OUT / CATALOG_NAME
    if catalog_gzip is None:
        # A missing optional gzip catalog is safe: verified bounded shards remain
        # the static-search transport. Remove any stale oversized copy.
        if catalog_path.exists():
            catalog_path.unlink()
    else:
        catalog_path.write_bytes(catalog_raw)

    manifest = {
        "version": 1,
        "integrity": "sha256",
        "updated_at": source.get("updated_at"),
        "total_pages": len(pages),
        "shard_count": len(shards),
        "shards": shards,
        "source": "Everything Hub public-web crawler",
        "notice": "Search metadata and short excerpts from publicly accessible pages."
    }
    if catalog_gzip is not None:
        manifest["catalog_gzip"] = catalog_gzip
    if query_index is not None:
        manifest["query_index"] = query_index
    elif query_index_omission_reason:
        manifest["query_index_omission_reason"] = query_index_omission_reason
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Built {len(shards)} shards for {len(pages)} pages.")
    for shard in shards:
        print(
            f'{shard["file"]}: {shard["pages"]} pages, '
            f'{shard["bytes"]} bytes, sha256 {shard["sha256"]}'
        )
    if catalog_gzip is not None:
        print(
            f'{CATALOG_NAME}: {catalog_gzip["pages"]} pages, '
            f'{catalog_gzip["bytes"]} compressed bytes, '
            f'sha256 {catalog_gzip["sha256"]}'
        )
    else:
        print(
            f"Compressed catalog omitted: {catalog_omission_reason}; "
            "verified shard fallback remains available."
        )
    if query_index is not None:
        print(
            f"Built query index: {query_index['token_count']:,} tokens, "
            f"{query_index['posting_count']:,} token-to-shard postings, "
            f"{query_index['shard_count']:,} shards, "
            f"{query_index['compressed_bytes']:,} compressed bytes."
        )
    else:
        print(f"Query index omitted: {query_index_omission_reason}")


if __name__ == "__main__":
    main()
