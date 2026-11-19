import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from catalog_search import MAX_CATALOG_GZIP_BYTES, _bounded_gzip, load_catalog


class CatalogLoaderTests(unittest.TestCase):
    def test_current_published_manifest_fits_python_loader_limit(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "public-search" / "manifest.json").read_text(encoding="utf-8"))
        catalog = manifest.get("catalog_gzip")
        self.assertIsInstance(catalog, dict)
        self.assertEqual(MAX_CATALOG_GZIP_BYTES, 10_000_000)
        self.assertLessEqual(catalog.get("bytes", 0), MAX_CATALOG_GZIP_BYTES)

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

    def write_shard_manifest(self, directory, shards):
        directory = Path(directory)
        entries = []
        for index, pages in enumerate(shards, start=1):
            raw = (json.dumps(
                {"version": 1, "pages": pages},
                separators=(",", ":"),
            ) + "\n").encode("utf-8")
            name = f"shard-{index:04d}.json"
            (directory / name).write_bytes(raw)
            entries.append({
                "file": name,
                "pages": len(pages),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
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
