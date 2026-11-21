import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import index_store


class CanonicalIndexStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "index.json"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def pages(count=300):
        return [
            {
                "url": f"https://example.test/page/{number}",
                "title": f"Page {number}",
                "description": "Representative metadata.",
                "text": "A searchable excerpt. " * 24,
                "robots_allowed": True,
            }
            for number in range(count)
        ]

    @staticmethod
    def pages_in_distinct_buckets(count=2, text_size=220):
        pages = []
        buckets = set()
        candidate = 0
        while len(pages) < count:
            url = f"https://example.test/aggregate/{candidate}"
            bucket = index_store.bucket_for_url(url)
            if bucket not in buckets:
                pages.append({
                    "url": url,
                    "title": "Aggregate limit test",
                    "text": "x" * text_size,
                })
                buckets.add(bucket)
            candidate += 1
        return pages

    def test_legacy_monolithic_index_remains_readable(self):
        payload = {
            "version": 1,
            "updated_at": 1700000000,
            "pages_indexed_this_run": 7,
            "pages": self.pages(3),
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(index_store.load_index(self.path), payload)

    def test_missing_index_uses_only_the_explicit_fallback(self):
        fallback = {"version": 1, "pages": []}
        self.assertEqual(index_store.load_index(self.path, fallback), fallback)
        with self.assertRaises(FileNotFoundError):
            index_store.load_index(self.path)

    def test_sharded_round_trip_replaces_large_pages_array_with_small_manifest(self):
        pages = self.pages(1200)
        payload = {
            "version": 1,
            "updated_at": 1700000000,
            "pages_indexed_this_run": 42,
            "fetch_attempts_this_run": 50,
            "pages": pages,
        }
        manifest = index_store.save_index(self.path, payload)
        stored = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(stored["storage_format"], index_store.FORMAT)
        self.assertNotIn("pages", stored)
        self.assertEqual(stored["total_pages"], len(pages))
        self.assertEqual(stored["shard_count"], len(stored["shards"]))
        self.assertGreater(stored["shard_count"], 1)
        self.assertLess(self.path.stat().st_size, 1_000_000)
        self.assertEqual(manifest["total_pages"], len(pages))

        loaded = index_store.load_index(self.path)
        self.assertEqual(loaded["updated_at"], payload["updated_at"])
        self.assertEqual(loaded["pages_indexed_this_run"], 42)
        self.assertEqual(
            {page["url"]: page for page in loaded["pages"]},
            {page["url"]: page for page in pages},
        )

    def test_shard_names_and_digests_are_deterministic(self):
        payload = {"version": 1, "updated_at": 1700000000, "pages": self.pages(400)}
        first = index_store.save_index(self.path, payload)
        first_digests = {row["file"]: row["sha256"] for row in first["shards"]}
        second = index_store.save_index(self.path, payload)
        second_digests = {row["file"]: row["sha256"] for row in second["shards"]}
        self.assertEqual(first_digests, second_digests)
        self.assertEqual(first, second)

    def test_corrupted_shard_is_rejected_before_json_is_used(self):
        index_store.save_index(
            self.path,
            {"version": 1, "updated_at": 1700000000, "pages": self.pages(20)},
        )
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        shard = self.root / "index-shards" / manifest["shards"][0]["file"]
        raw = shard.read_bytes()
        shard.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            index_store.load_index(self.path)

    def test_unsafe_shard_filename_is_rejected(self):
        index_store.save_index(
            self.path,
            {"version": 1, "updated_at": 1700000000, "pages": self.pages(5)},
        )
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        manifest["shards"][0]["file"] = "../index.json"
        self.path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsafe or duplicate shard"):
            index_store.load_index(self.path)

    def test_manifest_page_count_mismatch_is_rejected(self):
        index_store.save_index(
            self.path,
            {"version": 1, "updated_at": 1700000000, "pages": self.pages(5)},
        )
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        manifest["total_pages"] += 1
        self.path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "total_pages does not match"):
            index_store.load_index(self.path)

    def test_duplicate_urls_are_rejected_before_writing(self):
        page = {"url": "https://example.test/duplicate", "title": "Duplicate"}
        with self.assertRaisesRegex(ValueError, "duplicate URLs"):
            index_store.save_index(
                self.path,
                {"version": 1, "updated_at": 1700000000, "pages": [page, page]},
            )
        self.assertFalse(self.path.exists())

    def test_oversized_bucket_is_rejected_before_manifest_replacement(self):
        original = {"version": 1, "pages": self.pages(1)}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        page = {"url": "https://example.test/large", "text": "x" * 500}
        with patch.object(index_store, "MAX_SHARD_UNCOMPRESSED_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "uncompressed shard limit"):
                index_store.save_index(
                    self.path,
                    {"version": 1, "updated_at": 1700000001, "pages": [page]},
                )
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), original)

    def test_failed_update_keeps_the_previous_manifest_and_shards_readable(self):
        candidates = [
            (index_store.bucket_for_url(f"https://example.test/{number}"), f"https://example.test/{number}")
            for number in range(1000)
        ]
        ordered = sorted(candidates)
        low_bucket, low_url = ordered[0]
        high_bucket, high_url = ordered[-1]
        self.assertLess(low_bucket, high_bucket)

        old_page = {"url": low_url, "title": "old"}
        index_store.save_index(
            self.path,
            {"version": 1, "updated_at": 1700000000, "pages": [old_page]},
        )
        old_manifest = json.loads(self.path.read_text(encoding="utf-8"))
        old_file = self.root / "index-shards" / old_manifest["shards"][0]["file"]

        new_pages = [
            {"url": low_url, "title": "updated"},
            {"url": high_url, "text": "x" * 500},
        ]
        with patch.object(index_store, "MAX_SHARD_UNCOMPRESSED_BYTES", 200):
            with self.assertRaisesRegex(ValueError, "uncompressed shard limit"):
                index_store.save_index(
                    self.path,
                    {"version": 1, "updated_at": 1700000001, "pages": new_pages},
                )

        restored = index_store.load_index(self.path)
        self.assertEqual(restored["pages"], [old_page])
        self.assertTrue(old_file.is_file())

    def test_shard_metadata_matches_compressed_and_uncompressed_bytes(self):
        index_store.save_index(
            self.path,
            {"version": 1, "updated_at": 1700000000, "pages": self.pages(12)},
        )
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        for entry in manifest["shards"]:
            raw = (self.root / "index-shards" / entry["file"]).read_bytes()
            self.assertEqual(len(raw), entry["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
            decoded = gzip.decompress(raw)
            self.assertEqual(len(decoded), entry["uncompressed_bytes"])
            self.assertEqual(len(json.loads(decoded)["pages"]), entry["pages"])


    def test_aggregate_uncompressed_limit_rejects_save_without_replacing_manifest(self):
        old_page = {"url": "https://example.test/previous", "title": "previous"}
        index_store.save_index(
            self.path,
            {"version": 1, "updated_at": 1700000000, "pages": [old_page]},
        )
        old_manifest = json.loads(self.path.read_text(encoding="utf-8"))

        with patch.object(index_store, "MAX_TOTAL_UNCOMPRESSED_BYTES", 500):
            with self.assertRaisesRegex(ValueError, "aggregate uncompressed limit"):
                index_store.save_index(
                    self.path,
                    {
                        "version": 1,
                        "updated_at": 1700000001,
                        "pages": self.pages_in_distinct_buckets(),
                    },
                )

        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), old_manifest)
        self.assertEqual(index_store.load_index(self.path)["pages"], [old_page])

    def test_aggregate_decompression_limit_rejects_load(self):
        index_store.save_index(
            self.path,
            {
                "version": 1,
                "updated_at": 1700000000,
                "pages": self.pages_in_distinct_buckets(),
            },
        )
        with patch.object(index_store, "MAX_TOTAL_UNCOMPRESSED_BYTES", 500):
            with self.assertRaisesRegex(ValueError, "aggregate decompression limit"):
                index_store.load_index(self.path)



    def test_public_search_manifest_loads_verified_shards_for_catalog_analysis(self):
        public_dir = self.root / "public-search"
        public_dir.mkdir()
        manifest_path = public_dir / "manifest.json"
        pages = self.pages(3)
        payload = {"version": 1, "pages": pages}
        decoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\\n").encode("utf-8")
        raw = gzip.compress(decoded, compresslevel=9, mtime=0)
        shard_name = "shard-0001.json.gz"
        (public_dir / shard_name).write_bytes(raw)
        manifest = {
            "version": 1,
            "integrity": "sha256",
            "updated_at": 1700000000,
            "total_pages": len(pages),
            "shard_count": 1,
            "shards": [{
                "file": shard_name,
                "pages": len(pages),
                "bytes": len(raw),
                "uncompressed_bytes": len(decoded),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }],
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        loaded = index_store.load_index(manifest_path)
        self.assertEqual(loaded["total_pages"], len(pages))
        self.assertEqual(loaded["updated_at"], 1700000000)
        self.assertEqual(loaded["pages"], pages)

    def test_public_search_shard_digest_is_checked_before_json_use(self):
        public_dir = self.root / "public-search"
        public_dir.mkdir()
        manifest_path = public_dir / "manifest.json"
        pages = self.pages(1)
        decoded = (json.dumps({"version": 1, "pages": pages}, separators=(",", ":")) + "\\n").encode("utf-8")
        raw = gzip.compress(decoded, compresslevel=9, mtime=0)
        shard_name = "shard-0001.json.gz"
        (public_dir / shard_name).write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        manifest = {
            "version": 1, "integrity": "sha256", "updated_at": 1700000000,
            "total_pages": 1, "shard_count": 1,
            "shards": [{
                "file": shard_name, "pages": 1, "bytes": len(raw),
                "uncompressed_bytes": len(decoded), "sha256": hashlib.sha256(raw).hexdigest(),
            }],
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            index_store.load_index(manifest_path)

if __name__ == "__main__":
    unittest.main()
