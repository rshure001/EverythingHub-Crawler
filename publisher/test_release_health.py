import unittest

from release_health import MAX_CATALOG_GZIP_BYTES, build_health


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
        self.assertEqual(health["search_transport"], "gzip")


    def valid_media_catalog(self):
        return {
            "version": 1,
            "updated_at": 1794974899,
            "total_candidates": 2,
            "candidates": [
                {
                    "media_type": "image",
                    "url": "https://media.example.org/image.jpg",
                    "source_page_url": "https://media.example.org/story",
                    "source_pages": ["https://media.example.org/story"],
                    "title": "Example image",
                    "alt_text": "An image",
                    "thumbnail_url": "",
                    "robots_allowed": True,
                    "policy_status": "robots_allowed_not_fetched",
                    "rights_status": "unknown",
                    "usage_rights": "Unknown",
                    "first_seen_at": 100,
                    "last_seen_at": 100,
                    "seen_count": 1,
                },
                {
                    "media_type": "video",
                    "url": "https://media.example.org/video.mp4",
                    "source_page_url": "https://media.example.org/story",
                    "source_pages": ["https://media.example.org/story"],
                    "title": "Example video",
                    "alt_text": "",
                    "thumbnail_url": "",
                    "robots_allowed": True,
                    "policy_status": "robots_allowed_not_fetched",
                    "rights_status": "unknown",
                    "usage_rights": "Unknown",
                    "first_seen_at": 100,
                    "last_seen_at": 100,
                    "seen_count": 1,
                },
            ],
        }

    def test_health_includes_validated_media_inventory_without_urls(self):
        health = build_health(self.valid_manifest(), generated_at=1794975000,
                              media_catalog=self.valid_media_catalog())
        media = health["media_discovery"]
        self.assertEqual(media["total_candidates"], 2)
        self.assertEqual(media["by_type"], {"image": 1, "video": 1})
        self.assertEqual(media["robots_allowed"], 2)
        self.assertEqual(media["metadata_only_not_fetched"], 2)
        self.assertEqual(media["rights_unknown"], 2)
        self.assertEqual(media["extraction_status"], "not_performed_by_discovery_crawler")
        self.assertNotIn("url", media)

    def test_health_rejects_invalid_media_catalog(self):
        catalog = self.valid_media_catalog()
        catalog["candidates"][0]["rights_status"] = "licensed"
        with self.assertRaisesRegex(ValueError, "media catalog failed validation"):
            build_health(self.valid_manifest(), media_catalog=catalog)

    def test_health_supports_shard_only_manifest(self):
        manifest = self.valid_manifest()
        manifest.pop("catalog_gzip")
        health = build_health(manifest, generated_at=1794975000)

        self.assertIsNone(health["catalog_gzip"])
        self.assertEqual(health["search_transport"], "shards")

    def test_health_accepts_current_independent_search_catalog_size(self):
        manifest = self.valid_manifest()
        manifest["catalog_gzip"]["bytes"] = 6_310_389
        health = build_health(manifest)
        self.assertEqual(health["catalog_gzip"]["bytes"], 6_310_389)

    def test_health_accepts_catalog_at_independent_search_limit(self):
        manifest = self.valid_manifest()
        manifest["catalog_gzip"]["bytes"] = MAX_CATALOG_GZIP_BYTES
        health = build_health(manifest)
        self.assertEqual(health["catalog_gzip"]["bytes"], MAX_CATALOG_GZIP_BYTES)

    def test_health_rejects_catalog_over_independent_search_limit(self):
        manifest = self.valid_manifest()
        manifest["catalog_gzip"]["bytes"] = MAX_CATALOG_GZIP_BYTES + 1
        with self.assertRaisesRegex(ValueError, "8 MB independent-search safety limit"):
            build_health(manifest)

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
