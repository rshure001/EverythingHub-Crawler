#!/usr/bin/env python3
"""Build integrity-pinned public search shards and an optional compressed catalog."""
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from lane_classifier import VALID_LANES, classify_lane
from index_store import load_index

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "index.json"
OUT = ROOT / "public-search"
MAX_BYTES = 500_000  # Decoded shard target; gzip transport remains below 350 KB.
MAX_COMPRESSED_BYTES = 350_000
MAX_SHARD_COUNT = 640
SHARD_GZIP_LEVEL = 6
# The independent Vercel backend safely decompresses up to 64 MB. The
# Supabase Edge Function has a smaller 30 MB/6 MB budget and delegates larger
# catalogs to Vercel rather than scanning all shards in its CPU-limited isolate.
MAX_CATALOG_GZIP_BYTES = 16_000_000
MAX_CATALOG_UNCOMPRESSED_BYTES = 64_000_000
CATALOG_NAME = "catalog-v1.json.gz"


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
    if not isinstance(lane, str) or lane not in VALID_LANES:
        lane = classify_lane(url)
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
            if isinstance(value, (str, int, float)) and str(value).strip():
                compact_product[key] = str(value).strip()[:limit]
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


def main():
    source = load_index(SOURCE)
    pages = [compact(p) for p in source.get("pages", []) if is_publishable(p)]
    pages.sort(key=lambda p: (p["title"].casefold(), p["url"]))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("shard-*.json*"):
        old.unlink()

    shards = []
    batch = []
    batch_payload_size = 0
    number = 0

    def write_batch(items):
        nonlocal number
        if not items:
            return
        number += 1
        if number > MAX_SHARD_COUNT:
            raise ValueError("public search catalog exceeds the fixed 640-shard limit")
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

    prefix_bytes = len(b'{"version":1,"pages":[')
    suffix_bytes = len(b']}\n')
    for page in pages:
        page_size = encoded_page_size(page)
        candidate_size = (
            prefix_bytes + batch_payload_size
            + (1 if batch else 0) + page_size + suffix_bytes
        )
        if batch and candidate_size > MAX_BYTES:
            write_batch(batch)
            batch = []
            batch_payload_size = 0
            candidate_size = prefix_bytes + page_size + suffix_bytes
        if candidate_size > MAX_BYTES:
            raise ValueError("single compacted page exceeds the public shard size limit")
        batch.append(page)
        batch_payload_size += page_size + (1 if len(batch) > 1 else 0)
    write_batch(batch)

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


if __name__ == "__main__":
    main()
