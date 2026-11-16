import json
import tempfile
import unittest
from pathlib import Path

from validate_public_index import validate


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
            json.dumps({"version": 1, "pages": source_pages}),
            encoding="utf-8",
        )
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

    def test_valid_shards_match_canonical_index(self):
        pages = [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.org/b", "title": "B"},
        ]
        self.write_fixture(pages, pages)
        self.assertEqual(validate(self.root), [])

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
