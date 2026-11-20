import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from stage_search_catalog import stage_search_catalog, validate_manifest


def page(url, title):
    return {
        "url": url,
        "title": title,
        "description": "Verified public page",
        "text": "Everything Hub catalog test",
        "lane": "general",
    }


def write_shard(source, index, pages, compressed=True):
    decoded = json.dumps({"version": 1, "pages": pages}, separators=(",", ":")).encode("utf-8")
    name = f"shard-{index:04d}.json.gz" if compressed else f"shard-{index:04d}.json"
    raw = gzip.compress(decoded, compresslevel=9, mtime=0) if compressed else decoded
    (source / name).write_bytes(raw)
    meta = {
        "file": name,
        "pages": len(pages),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if compressed:
        meta["uncompressed_bytes"] = len(decoded)
    return meta


def write_manifest(source, shards, pages, catalog_gzip=None):
    manifest = {
        "version": 1,
        "integrity": "sha256",
        "updated_at": 1795140000,
        "total_pages": len(pages),
        "shard_count": len(shards),
        "shards": shards,
    }
    if catalog_gzip is not None:
        manifest["catalog_gzip"] = catalog_gzip
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class StageSearchCatalogTests(unittest.TestCase):
    def test_stages_compressed_shards_and_matching_optional_catalog(self):
        pages = [
            page("https://example.com/one", "One"),
            page("https://example.org/two", "Two"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "public-search"
            target = root / "site" / "search-index"
            source.mkdir()
            shards = [
                write_shard(source, 1, [pages[0]], compressed=True),
                write_shard(source, 2, [pages[1]], compressed=True),
            ]
            catalog_raw = gzip.compress(
                json.dumps({"version": 1, "pages": pages}, separators=(",", ":")).encode("utf-8"),
                compresslevel=9,
                mtime=0,
            )
            catalog_meta = {
                "file": "catalog-v1.json.gz",
                "pages": len(pages),
                "bytes": len(catalog_raw),
                "sha256": hashlib.sha256(catalog_raw).hexdigest(),
            }
            (source / catalog_meta["file"]).write_bytes(catalog_raw)
            write_manifest(source, shards, pages, catalog_meta)

            result = stage_search_catalog(source, target)

            self.assertEqual(result["status"], "validated-and-staged")
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["shards"], 2)
            self.assertEqual(result["search_transport"], "gzip")
            self.assertTrue((target / "shard-0001.json.gz").is_file())
            self.assertTrue((target / "catalog-v1.json.gz").is_file())
            self.assertTrue((target / "manifest.json").is_file())

    def test_stages_legacy_raw_shards_during_rollout(self):
        pages = [page("https://example.com/legacy", "Legacy")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "public-search"
            target = root / "site" / "search-index"
            source.mkdir()
            shards = [write_shard(source, 1, pages, compressed=False)]
            write_manifest(source, shards, pages)

            result = stage_search_catalog(source, target)

            self.assertEqual(result["search_transport"], "shards")
            self.assertTrue((target / "shard-0001.json").is_file())

    def test_sha256_is_checked_before_attempting_to_decompress_tampered_gzip(self):
        pages = [page("https://example.com/tampered", "Tampered")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "public-search"
            target = root / "site" / "search-index"
            source.mkdir()
            shard = write_shard(source, 1, pages, compressed=True)
            raw_path = source / shard["file"]
            raw = bytearray(raw_path.read_bytes())
            raw[-1] ^= 1
            raw_path.write_bytes(raw)
            write_manifest(source, [shard], pages)

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                stage_search_catalog(source, target)
            self.assertFalse(target.exists())

    def test_rejects_duplicate_urls_across_shards(self):
        duplicate = page("https://example.com/duplicate", "Duplicate")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "public-search"
            target = root / "site" / "search-index"
            source.mkdir()
            shards = [
                write_shard(source, 1, [duplicate], compressed=True),
                write_shard(source, 2, [{**duplicate, "title": "Duplicate copy"}], compressed=True),
            ]
            write_manifest(source, shards, [duplicate, {**duplicate, "title": "Duplicate copy"}])

            with self.assertRaisesRegex(ValueError, "duplicate URL"):
                stage_search_catalog(source, target)

    def test_rejects_mixed_raw_and_compressed_shards(self):
        pages = [
            page("https://example.com/raw", "Raw"),
            page("https://example.org/gzip", "Gzip"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "public-search"
            target = root / "site" / "search-index"
            source.mkdir()
            shards = [
                write_shard(source, 1, [pages[0]], compressed=False),
                write_shard(source, 2, [pages[1]], compressed=True),
            ]
            write_manifest(source, shards, pages)

            with self.assertRaisesRegex(ValueError, "mixes compressed and legacy"):
                stage_search_catalog(source, target)


    def test_accepts_catalog_above_100000_pages_within_new_safety_limit(self):
        shards = []
        remaining = 125_643
        for index in range(1, 127):
            pages = min(1000, remaining)
            remaining -= pages
            shards.append({
                "file": f"shard-{index:04d}.json.gz",
                "pages": pages,
                "bytes": 1,
                "uncompressed_bytes": 1,
                "sha256": "a" * 64,
            })
        manifest = {
            "version": 1,
            "integrity": "sha256",
            "updated_at": 1795140000,
            "total_pages": 125_643,
            "shard_count": len(shards),
            "shards": shards,
        }
        checked = validate_manifest(manifest)
        self.assertEqual(checked["total_pages"], 125_643)
        self.assertEqual(len(checked["shards"]), 126)

if __name__ == "__main__":
    unittest.main()
