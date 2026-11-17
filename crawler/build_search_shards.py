#!/usr/bin/env python3
"""Build integrity-pinned public search shards and one compressed crawler catalog."""
import gzip
import hashlib
import json
from pathlib import Path

from lane_classifier import VALID_LANES, classify_lane

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "index.json"
OUT = ROOT / "public-search"
MAX_BYTES = 350_000
CATALOG_NAME = "catalog-v1.json.gz"


def is_publishable(doc):
    """Exclude explicitly robots-disallowed records from public search shards."""
    if not isinstance(doc, dict) or doc.get("robots_allowed") is False:
        return False
    url = doc.get("url")
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def compact(doc):
    url = str(doc.get("url", ""))[:2048]
    lane = doc.get("lane")
    if not isinstance(lane, str) or lane not in VALID_LANES:
        lane = classify_lane(url)
    return {
        "url": url,
        "title": str(doc.get("title", ""))[:500],
        "description": str(doc.get("description", ""))[:500],
        "text": str(doc.get("text", ""))[:1600],
        "lane": lane,
    }


def encode_batch(items):
    """Serialize exactly the bytes written to disk, including the final newline."""
    payload = json.dumps(
        {"version": 1, "pages": items},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    return payload.encode("utf-8")


def batch_fits(items, max_bytes=MAX_BYTES):
    """Use on-disk byte size so the builder and validator share one boundary."""
    return len(encode_batch(items)) <= max_bytes


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
    (OUT / CATALOG_NAME).write_bytes(catalog_raw)
    catalog_gzip = {
        "file": CATALOG_NAME,
        "pages": len(pages),
        "bytes": len(catalog_raw),
        "sha256": hashlib.sha256(catalog_raw).hexdigest(),
    }

    manifest = {
        "version": 1,
        "integrity": "sha256",
        "updated_at": source.get("updated_at"),
        "total_pages": len(pages),
        "shard_count": len(shards),
        "shards": shards,
        "catalog_gzip": catalog_gzip,
        "source": "Everything Hub public-web crawler",
        "notice": "Search metadata and short excerpts from publicly accessible pages."
    }
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
    print(
        f'{CATALOG_NAME}: {catalog_gzip["pages"]} pages, '
        f'{catalog_gzip["bytes"]} compressed bytes, '
        f'sha256 {catalog_gzip["sha256"]}'
    )


if __name__ == "__main__":
    main()
