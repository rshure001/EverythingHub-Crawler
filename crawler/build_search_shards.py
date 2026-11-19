#!/usr/bin/env python3
"""Build integrity-pinned public search shards and an optional compressed catalog."""
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from lane_classifier import VALID_LANES, classify_lane

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "index.json"
OUT = ROOT / "public-search"
MAX_BYTES = 340_000  # Keep 10 KB headroom below the 350 KB serving/validation ceiling.
# Vercel's independent search backend and the browser client accept a 7 MB catalog.
# The Supabase Edge Function intentionally keeps its stricter 6 MB CPU guard.
MAX_CATALOG_GZIP_BYTES = 7_000_000
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
    """Measure exact on-disk bytes while keeping below the public hard ceiling."""
    return len(encode_batch(items)) <= max_bytes


def catalog_metadata(raw, page_count):
    """Return gzip metadata only when the optional catalog stays within its serving cap."""
    if len(raw) > MAX_CATALOG_GZIP_BYTES:
        return None
    return {
        "file": CATALOG_NAME,
        "pages": page_count,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    pages = [compact(p) for p in source.get("pages", []) if is_publishable(p)]
    pages.sort(key=lambda p: (p["title"].casefold(), p["url"]))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("shard-*.json"):
        old.unlink()

    shards = []
    batch = []
    number = 0

    def write_batch(items):
        nonlocal number
        if not items:
            return
        number += 1
        name = f"shard-{number:04d}.json"
        raw = encode_batch(items)
        (OUT / name).write_bytes(raw)
        shards.append({
            "file": name,
            "pages": len(items),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })

    for page in pages:
        trial = batch + [page]
        if batch and not batch_fits(trial):
            write_batch(batch)
            batch = [page]
            if not batch_fits(batch):
                raise ValueError("single compacted page exceeds the public shard size limit")
        else:
            batch = trial
    write_batch(batch)

    catalog_payload = json.dumps(
        {"version": 1, "pages": pages},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    catalog_raw = gzip.compress(catalog_payload.encode("utf-8"), compresslevel=9, mtime=0)
    catalog_gzip = catalog_metadata(catalog_raw, len(pages))
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
            f"Compressed catalog omitted: {len(catalog_raw):,} bytes exceeds "
            f"the {MAX_CATALOG_GZIP_BYTES:,}-byte limit; verified shard fallback remains available."
        )


if __name__ == "__main__":
    main()
