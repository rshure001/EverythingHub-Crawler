import gzip
import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from pathlib import Path

os.environ["EH_SKIP_INDEX_LOAD"] = "1"
MODULE_PATH = pathlib.Path(__file__).with_name("static_search_api.py")
SPEC = importlib.util.spec_from_file_location("eh_static_search", MODULE_PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class StaticSearchTests(unittest.TestCase):
    def setUp(self):
        pages = [
            {"url": "https://nasa.gov/moon", "title": "NASA Apollo Moon Mission",
             "description": "Space exploration mission", "text": "NASA Apollo lunar mission",
             "lane": "science"},
            {"url": "https://example.com/rolex", "title": "Rolex Watches",
             "description": "Luxury watch information", "text": "Rolex watch models",
             "lane": "business"},
            {"url": "https://example.com/nasa-news", "title": "NASA latest news",
             "description": "NASA agency update", "text": "NASA space science",
             "lane": "news"},
        ]
        api.DOCS = []
        from collections import Counter
        import re
        for page in pages:
            tok = lambda value: Counter(re.findall(r"[a-z0-9]{2,}", value.lower()))
            api.DOCS.append((page, tok(page["title"]), tok(page["description"]),
                             tok(page["text"]), tok(page["url"])))

    def make_catalog(self, root, pages):
        payload = json.dumps(
            {"version": 1, "pages": pages},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        compressed = gzip.compress(payload, mtime=0)
        filename = "shard-0001.json.gz"
        (root / filename).write_bytes(compressed)
        manifest = {
            "version": 1,
            "integrity": "sha256",
            "updated_at": 1795068644,
            "total_pages": len(pages),
            "shard_count": 1,
            "shards": [{
                "file": filename,
                "pages": len(pages),
                "bytes": len(compressed),
                "uncompressed_bytes": len(payload),
                "sha256": hashlib.sha256(compressed).hexdigest(),
            }],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_capacity_bounds_cover_current_public_catalog(self):
        root = Path(__file__).resolve().parents[1]
        manifest_path = root / "public-search" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shards = manifest["shards"]
        pages = api._manifest_int(manifest["total_pages"], "total_pages", 1, api.MAX_PAGES)
        shard_count = api._manifest_int(manifest["shard_count"], "shard_count", 1, api.MAX_SHARDS)
        max_pages_per_shard = max(
            api._manifest_int(item["pages"], "shard pages", 1, api.MAX_PAGES_PER_SHARD)
            for item in shards
        )
        compressed_bytes = sum(
            api._manifest_int(item["bytes"], "shard bytes", 1, api.MAX_SHARD_BYTES)
            for item in shards
        )
        uncompressed_bytes = sum(
            api._manifest_int(item["uncompressed_bytes"], "shard uncompressed bytes", 1, api.MAX_SHARD_UNCOMPRESSED_BYTES)
            for item in shards
        )
        manifest_bytes = manifest_path.stat().st_size

        self.assertGreater(pages, 180_000)
        self.assertEqual(shard_count, len(shards))
        self.assertLessEqual(compressed_bytes, api.MAX_TOTAL_COMPRESSED_BYTES)
        self.assertLessEqual(uncompressed_bytes, api.MAX_TOTAL_UNCOMPRESSED_BYTES)
        self.assertEqual(
            api._manifest_int(manifest_bytes, "manifest bytes", 1, api.MAX_MANIFEST_BYTES),
            manifest_bytes,
        )
        self.assertEqual(api.MAX_PAGES, 250_000)
        self.assertEqual(api.MAX_SHARDS, 900)
        self.assertEqual(api.MAX_PAGES_PER_SHARD, 2_000)
        self.assertGreater(max_pages_per_shard, 0)
        self.assertLessEqual(max_pages_per_shard, api.MAX_PAGES_PER_SHARD)
        self.assertEqual(api.MAX_SHARD_UNCOMPRESSED_BYTES, 600_000)
        self.assertEqual(api.MAX_MANIFEST_BYTES, 256_000)
        self.assertEqual(api.MAX_TOTAL_COMPRESSED_BYTES, 150_000_000)
        self.assertEqual(api.MAX_TOTAL_UNCOMPRESSED_BYTES, 540_000_000)

    def test_capacity_rejects_values_above_publisher_envelope(self):
        limits = [
            (250_001, "total_pages", api.MAX_PAGES),
            (901, "shard_count", api.MAX_SHARDS),
            (2_001, "shard pages", api.MAX_PAGES_PER_SHARD),
            (150_000_001, "total compressed bytes", api.MAX_TOTAL_COMPRESSED_BYTES),
            (540_000_001, "total uncompressed bytes", api.MAX_TOTAL_UNCOMPRESSED_BYTES),
            (256_001, "manifest bytes", api.MAX_MANIFEST_BYTES),
        ]
        for value, name, maximum in limits:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "safe bounds"):
                    api._manifest_int(value, name, 1, maximum)

    def test_title_hits_rank_first(self):
        result = api.search("Apollo", "", 10, 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["url"], "https://nasa.gov/moon")

    def test_lane_filter(self):
        result = api.search("NASA", "news", 10, 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["tentacle"], "news")

    def test_pagination(self):
        result = api.search("NASA", "", 1, 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertFalse(result["hasMore"])

    def test_no_match(self):
        result = api.search("nonexistentterm", "", 10, 0)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])

    def test_query_terms_match_without_case_sensitivity(self):
        result = api.search("nAsA", "", 10, 0)
        self.assertEqual(result["total"], 2)

    def test_load_index_reads_and_verifies_compressed_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.make_catalog(root, [{
                "url": "https://example.com/nasa",
                "title": "NASA research",
                "description": "Space science",
                "text": "Verified compressed shard",
                "lane": "science",
            }])

            pages = api.load_index(root)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["title"], "NASA research")
        self.assertEqual(pages[0]["lane"], "science")

    def test_load_index_rejects_a_tampered_shard_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.make_catalog(root, [{"url": "https://example.com/a", "title": "A"}])
            shard = root / "shard-0001.json.gz"
            shard.write_bytes(shard.read_bytes() + b"x")

            with self.assertRaisesRegex(ValueError, "compressed byte count|SHA-256"):
                api.load_index(root)

    def test_load_index_rejects_manifest_page_total_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.make_catalog(
                root,
                [{"url": "https://example.com/a", "title": "A"}],
            )
            manifest["total_pages"] = 2
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "page total"):
                api.load_index(root)

    def test_load_index_rejects_oversized_decoded_shard_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.make_catalog(
                root,
                [{"url": "https://example.com/a", "title": "A"}],
            )
            manifest["shards"][0]["uncompressed_bytes"] = api.MAX_SHARD_UNCOMPRESSED_BYTES + 1
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "uncompressed bytes"):
                api.load_index(root)

    def test_load_index_rejects_robots_denied_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.make_catalog(root, [{
                "url": "https://example.com/blocked",
                "title": "Blocked",
                "robots_allowed": False,
            }])

            with self.assertRaisesRegex(ValueError, "robots-denied"):
                api.load_index(root)


if __name__ == "__main__":
    unittest.main()
