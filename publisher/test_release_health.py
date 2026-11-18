import unittest

from release_health import build_health


class ReleaseHealthTests(unittest.TestCase):
    def valid_manifest(self):
        return {
            "version": 1,
            "integrity": "sha256",
            "updated_at": 1794974899,
            "total_pages": 5724,
            "shard_count": 32,
            "shards": [{} for _ in range(32)],
            "catalog_gzip": {
                "file": "catalog-v1.json.gz",
                "pages": 5724,
                "bytes": 2233460,
                "sha256": "a" * 64,
            },
        }

    def test_health_record_matches_verified_catalog_fields(self):
        health = build_health(self.valid_manifest(), generated_at=1794975000)
        self.assertEqual(health["schema"], 1)
        self.assertEqual(health["generated_at"], 1794975000)
        self.assertEqual(health["source_updated_at"], 1794974899)
        self.assertEqual(health["total_pages"], 5724)
        self.assertEqual(health["shard_count"], 32)
        self.assertEqual(health["catalog_gzip"]["sha256"], "a" * 64)

    def test_health_rejects_shard_count_mismatch(self):
        manifest = self.valid_manifest()
        manifest["shard_count"] = 31
        with self.assertRaisesRegex(ValueError, "shard_count"):
            build_health(manifest)

    def test_health_rejects_catalog_page_count_mismatch(self):
        manifest = self.valid_manifest()
        manifest["catalog_gzip"]["pages"] = 5723
        with self.assertRaisesRegex(ValueError, "page count"):
            build_health(manifest)

    def test_health_rejects_invalid_catalog_digest(self):
        manifest = self.valid_manifest()
        manifest["catalog_gzip"]["sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            build_health(manifest)

    def test_health_rejects_non_sha256_manifest(self):
        manifest = self.valid_manifest()
        manifest["integrity"] = "none"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            build_health(manifest)


if __name__ == "__main__":
    unittest.main()
