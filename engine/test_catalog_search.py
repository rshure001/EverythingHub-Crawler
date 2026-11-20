import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from catalog_search import (MAX_CATALOG_GZIP_BYTES, MAX_INDEXED_PAGES, MAX_SHARD_BYTES, MAX_SHARD_COUNT, MAX_SHARD_UNCOMPRESSED_BYTES, MAX_TOTAL_SHARD_BYTES, MAX_TOTAL_COMPRESSED_SHARD_BYTES, MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES, _bounded_gzip, load_catalog)


class CatalogLoaderTests(unittest.TestCase):
    def test_current_published_manifest_loads_within_bounded_shard_limits(self):
        root = Path(__file__).resolve().parents[1]
        search_dir = root / "public-search"
        manifest = json.loads((search_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(MAX_CATALOG_GZIP_BYTES, 16_000_000)
        self.assertEqual(MAX_INDEXED_PAGES, 100_000)
        self.assertEqual(MAX_SHARD_COUNT, 512)
        self.assertEqual(MAX_SHARD_BYTES, 350_000)
        self.assertEqual(MAX_SHARD_UNCOMPRESSED_BYTES, 500_000)
        self.assertEqual(MAX_TOTAL_SHARD_BYTES, 100_000_000)
        self.assertEqual(MAX_TOTAL_COMPRESSED_SHARD_BYTES, 64_000_000)
        self.assertEqual(MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES, 256_000_000)
        self.assertGreaterEqual(manifest["total_pages"], 21_000)
        self.assertLessEqual(manifest["total_pages"], MAX_INDEXED_PAGES)
        self.assertEqual(
            sum(item["pages"] for item in manifest["shards"]),
            manifest["total_pages"],
        )
        total_shard_bytes = sum(item["bytes"] for item in manifest["shards"])
        if all(item["file"].endswith(".json.gz") for item in manifest["shards"]):
            self.assertLessEqual(total_shard_bytes, MAX_TOTAL_COMPRESSED_SHARD_BYTES)
        else:
            self.assertLessEqual(total_shard_bytes, MAX_TOTAL_SHARD_BYTES)
        catalog_gzip = manifest.get("catalog_gzip")
        if catalog_gzip is not None:
            self.assertIsInstance(catalog_gzip, dict)
            self.assertEqual(catalog_gzip.get("pages"), manifest["total_pages"])
            self.assertLessEqual(catalog_gzip.get("bytes", MAX_CATALOG_GZIP_BYTES + 1), MAX_CATALOG_GZIP_BYTES)
        else:
            self.assertGreaterEqual(manifest["shard_count"], 1)
        pages = load_catalog(search_dir=search_dir)
        self.assertEqual(len(pages), manifest["total_pages"])

    def test_loads_version_one_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"version": 1, "pages": [{"url": "https://example.com"}]}, stream)
            self.assertEqual(load_catalog(path), [{"url": "https://example.com"}])

    def test_bounded_gzip_accepts_catalog_above_30mb_within_64mb_limit(self):
        payload = b"x" * 30_000_001
        decoded = _bounded_gzip(gzip.compress(payload))
        self.assertEqual(len(decoded), len(payload))
        self.assertEqual(decoded[:1], b"x")

    def test_rejects_unknown_catalog_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"version": 2, "pages": []}, stream)
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_rejects_non_list_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"version": 1, "pages": {}}, stream)
            with self.assertRaises(ValueError):
                load_catalog(path)

    def write_shard_manifest(self, directory, shards, compressed=True):
        directory = Path(directory)
        entries = []
        for index, pages in enumerate(shards, start=1):
            uncompressed = (json.dumps(
                {"version": 1, "pages": pages},
                separators=(",", ":"),
            ) + "\n").encode("utf-8")
            raw = gzip.compress(uncompressed, compresslevel=9, mtime=0) if compressed else uncompressed
            name = f"shard-{index:04d}.json.gz" if compressed else f"shard-{index:04d}.json"
            (directory / name).write_bytes(raw)
            entry = {
                "file": name,
                "pages": len(pages),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            if compressed:
                entry["uncompressed_bytes"] = len(uncompressed)
            entries.append(entry)
        manifest = {
            "version": 1,
            "integrity": "sha256",
            "updated_at": 1700000000,
            "total_pages": sum(len(pages) for pages in shards),
            "shard_count": len(entries),
            "shards": entries,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_legacy_raw_shards_remain_readable_during_rollout(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = [{"url": "https://example.com/legacy", "title": "Legacy"}]
            self.write_shard_manifest(directory, [expected], compressed=False)
            self.assertEqual(load_catalog(search_dir=directory), expected)

    def test_loads_and_verifies_shard_only_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.org/b", "title": "B"},
            ]
            self.write_shard_manifest(directory, [[expected[0]], [expected[1]]])
            self.assertEqual(load_catalog(search_dir=directory), expected)

    def test_rejects_shard_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_shard_manifest(directory, [[{"url": "https://example.com/a"}]])
            path = Path(directory) / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["shards"][0]["sha256"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                load_catalog(search_dir=directory)

    def test_rejects_duplicate_urls_across_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            page = {"url": "https://example.com/a"}
            self.write_shard_manifest(directory, [[page], [page]])
            with self.assertRaisesRegex(ValueError, "duplicate URL"):
                load_catalog(search_dir=directory)


    def test_rejects_manifest_above_page_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {
                "version": 1, "integrity": "sha256", "updated_at": 1700000000,
                "total_pages": MAX_INDEXED_PAGES + 1, "shard_count": 1,
                "shards": [{}],
            }
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "total_pages exceeds the safety limit"):
                load_catalog(search_dir=directory)

    def test_rejects_manifest_above_shard_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {
                "version": 1, "integrity": "sha256", "updated_at": 1700000000,
                "total_pages": 1, "shard_count": MAX_SHARD_COUNT + 1,
                "shards": [{} for _ in range(MAX_SHARD_COUNT + 1)],
            }
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "shard_count"):
                load_catalog(search_dir=directory)

    def test_rejects_aggregate_shard_bytes_above_limit_before_reading_files(self):
        with tempfile.TemporaryDirectory() as directory:
            entries = [{
                "file": f"shard-{index:04d}.json",
                "pages": 1,
                "bytes": MAX_SHARD_BYTES,
                "sha256": "0" * 64,
            } for index in range(1, MAX_SHARD_COUNT + 1)]
            manifest = {
                "version": 1, "integrity": "sha256", "updated_at": 1700000000,
                "total_pages": MAX_SHARD_COUNT, "shard_count": len(entries),
                "shards": entries,
            }
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "aggregate byte safety limit"):
                load_catalog(search_dir=directory)

    def test_rejects_shard_page_count_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_shard_manifest(directory, [[{"url": "https://example.com/a"}]])
            path = Path(directory) / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["total_pages"] = 2
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "page count differs"):
                load_catalog(search_dir=directory)


if __name__ == "__main__":
    unittest.main()
