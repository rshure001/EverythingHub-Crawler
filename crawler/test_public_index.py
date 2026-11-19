import gzip
import json
import tempfile
import unittest
from pathlib import Path

from build_search_shards import is_publishable
from validate_public_index import (
    MAX_CANONICAL_INDEX_BYTES,
    RECOMMENDED_CANONICAL_INDEX_BYTES,
    bounded_gzip_decompress,
    canonical_index_size_warning,
    validation_summary,
    validate,
)


class PublicIndexValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        (self.root / "public-search").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_fixture(self, source_pages, shard_pages):
        import hashlib

        (self.root / "data" / "index.json").write_text(
            json.dumps({"version": 1, "updated_at": 1700000000, "pages": source_pages}),
            encoding="utf-8",
        )
        # Public search shards now require one of the eight supported lanes.
        shard_pages = [
            {
                **page,
                "lane": page.get("lane") if page.get("lane") in {
                    "general", "science", "news", "technology",
                    "business", "government", "culture", "nature",
                } else "general",
            }
            for page in shard_pages
        ]
        raw = (
            json.dumps(
                {"version": 1, "pages": shard_pages},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        (self.root / "public-search" / "shard-0001.json").write_bytes(raw)
        manifest = {
            "version": 1,
            "integrity": "sha256",
            "updated_at": 1700000000,
            "total_pages": len(shard_pages),
            "shard_count": 1,
            "shards": [{
                "file": "shard-0001.json",
                "pages": len(shard_pages),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }],
        }
        (self.root / "public-search" / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )


    def test_canonical_index_size_warning_is_silent_below_recommendation(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        self.assertIsNone(canonical_index_size_warning(self.root))

    def test_canonical_index_size_warning_reports_github_recommendation(self):
        source_path = self.root / "data" / "index.json"
        with source_path.open("wb") as handle:
            handle.truncate(RECOMMENDED_CANONICAL_INDEX_BYTES + 1)
        warning = canonical_index_size_warning(self.root)
        self.assertIsNotNone(warning)
        self.assertIn("GitHub recommends", warning)
        self.assertIn(f"{RECOMMENDED_CANONICAL_INDEX_BYTES:,}", warning)

    def test_canonical_index_hard_safety_ceiling_fails_before_json_parse(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        source_path = self.root / "data" / "index.json"
        with source_path.open("wb") as handle:
            handle.truncate(MAX_CANONICAL_INDEX_BYTES + 1)
        errors = validate(self.root)
        self.assertEqual(len(errors), 1)
        self.assertIn("safety ceiling", errors[0])

    def test_manifest_timestamp_must_match_canonical_index(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        manifest_path = self.root / "public-search" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["updated_at"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIn(
            "manifest updated_at does not match canonical index",
            validate(self.root),
        )

    def test_manifest_timestamp_must_be_a_positive_unix_integer(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        for value in (None, True, 0, -1, "1700000000"):
            with self.subTest(value=value):
                self.write_fixture(pages, pages)
                manifest_path = self.root / "public-search" / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["updated_at"] = value
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                self.assertIn(
                    "manifest updated_at must be a positive Unix timestamp",
                    validate(self.root),
                )

    def test_canonical_index_timestamp_must_be_a_positive_unix_integer(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        source_path = self.root / "data" / "index.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["updated_at"] = True
        source_path.write_text(json.dumps(source), encoding="utf-8")
        self.assertIn(
            "canonical index updated_at must be a positive Unix timestamp",
            validate(self.root),
        )

    def test_bounded_gzip_decompression_accepts_valid_payload(self):
        raw = gzip.compress(b"valid catalog", mtime=0)
        self.assertEqual(bounded_gzip_decompress(raw, 32), b"valid catalog")

    def test_bounded_gzip_decompression_rejects_oversized_output(self):
        raw = gzip.compress(b"x" * 100, mtime=0)
        with self.assertRaisesRegex(ValueError, "safety limit"):
            bounded_gzip_decompress(raw, 50)

    def test_bounded_gzip_decompression_rejects_truncated_or_trailing_data(self):
        raw = gzip.compress(b"valid catalog", mtime=0)
        with self.assertRaisesRegex(ValueError, "incomplete or invalid"):
            bounded_gzip_decompress(raw[:-2], 100)
        with self.assertRaisesRegex(ValueError, "trailing data"):
            bounded_gzip_decompress(raw + b"extra", 100)

    def test_valid_shards_match_canonical_index(self):
        pages = [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.org/b", "title": "B"},
        ]
        self.write_fixture(pages, pages)
        self.assertEqual(validate(self.root), [])

    def test_validation_summary_supports_shard_only_manifest(self):
        summary = validation_summary({"total_pages": 12749, "shard_count": 72})
        self.assertIn("compressed catalog omitted", summary)
        self.assertIn("verified shard fallback remains available", summary)

    def test_validation_summary_reports_verified_compressed_catalog(self):
        summary = validation_summary({
            "total_pages": 2,
            "shard_count": 1,
            "catalog_gzip": {"bytes": 1234},
        })
        self.assertIn("compressed catalog verified (1,234 bytes)", summary)

    def test_robots_disallowed_records_are_never_published(self):
        blocked = {"url": "https://example.com/private", "robots_allowed": False}
        allowed = {"url": "https://example.com/public", "robots_allowed": True}
        legacy = {"url": "https://example.com/legacy"}
        self.assertFalse(is_publishable(blocked))
        self.assertTrue(is_publishable(allowed))
        self.assertTrue(is_publishable(legacy))

    def test_credential_bearing_urls_are_not_publishable(self):
        for url in (
            "https://user:secret@example.com/private",
            "https://@example.com/private",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_publishable({"url": url}))

    def test_credential_bearing_urls_are_rejected_from_public_shards(self):
        url = "https://user:secret@example.com/private"
        page = {"url": url, "title": "Credential-bearing URL"}
        self.write_fixture([page], [page])
        errors = validate(self.root)
        self.assertTrue(any("credential-bearing URL" in error for error in errors))

    def test_duplicate_urls_are_rejected(self):
        source = [{"url": "https://example.com/a", "title": "A"}]
        shards = [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.com/a", "title": "A duplicate"},
        ]
        self.write_fixture(source, shards)
        self.assertTrue(any("duplicate URL" in error for error in validate(self.root)))

    def test_non_web_urls_are_rejected(self):
        source = [{"url": "https://example.com/a", "title": "A"}]
        shards = [{"url": "javascript:alert(1)", "title": "Bad"}]
        self.write_fixture(source, shards)
        self.assertTrue(any("non-public-web URL" in error for error in validate(self.root)))

    def test_shard_byte_mismatch_is_rejected(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        manifest_path = self.root / "public-search" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["shards"][0]["bytes"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertTrue(any("byte-size mismatch" in error for error in validate(self.root)))

    def test_sha256_detects_same_size_shard_tampering(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        shard_path = self.root / "public-search" / "shard-0001.json"
        original = shard_path.read_bytes()
        tampered = original.replace(b'"title":"A"', b'"title":"B"')
        self.assertEqual(len(original), len(tampered))
        shard_path.write_bytes(tampered)
        errors = validate(self.root)
        self.assertTrue(any("SHA-256 mismatch" in error for error in errors))

    def test_sha256_integrity_requires_each_digest(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        manifest_path = self.root / "public-search" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        del manifest["shards"][0]["sha256"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertTrue(any("missing SHA-256 digest" in error for error in validate(self.root)))

    def test_legacy_manifest_without_integrity_remains_compatible(self):
        pages = [{"url": "https://example.com/a", "title": "A"}]
        self.write_fixture(pages, pages)
        manifest_path = self.root / "public-search" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("integrity")
        manifest["shards"][0].pop("sha256")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(validate(self.root), [])


if __name__ == "__main__":
    unittest.main()
