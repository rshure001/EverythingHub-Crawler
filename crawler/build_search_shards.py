#!/usr/bin/env python3
"""Build small, public, read-only search data shards from the crawler index."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "index.json"
OUT = ROOT / "public-search"
MAX_BYTES = 350_000

def compact(doc):
    return {
        "url": str(doc.get("url", ""))[:2048],
        "title": str(doc.get("title", ""))[:500],
        "description": str(doc.get("description", ""))[:500],
        "text": str(doc.get("text", ""))[:1600],
    }

def main():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    pages = [compact(p) for p in source.get("pages", [])
             if isinstance(p, dict) and p.get("url", "").startswith(("http://", "https://"))]
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
        payload = json.dumps({"version": 1, "pages": items}, ensure_ascii=False,
                             separators=(",", ":")) + "\n"
        (OUT / name).write_text(payload, encoding="utf-8")
        shards.append({"file": name, "pages": len(items),
                       "bytes": len(payload.encode("utf-8"))})
    for page in pages:
        trial = batch + [page]
        encoded = json.dumps({"version": 1, "pages": trial}, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        if batch and len(encoded) > MAX_BYTES:
            write_batch(batch)
            batch = [page]
        else:
            batch = trial
    write_batch(batch)
    manifest = {
        "version": 1,
        "updated_at": source.get("updated_at"),
        "total_pages": len(pages),
        "shard_count": len(shards),
        "shards": shards,
        "source": "Everything Hub public-web crawler",
        "notice": "Search metadata and short excerpts from publicly accessible pages."
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(shards)} shards for {len(pages)} pages.")
    for shard in shards:
        print(f'{shard["file"]}: {shard["pages"]} pages, {shard["bytes"]} bytes')

if __name__ == "__main__":
    main()
