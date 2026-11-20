import unittest

from validate_public_index import (
    MAX_CATALOG_GZIP_BYTES as VALIDATOR_MAX_CATALOG_GZIP_BYTES,
    MAX_CATALOG_UNCOMPRESSED_BYTES as VALIDATOR_MAX_CATALOG_UNCOMPRESSED_BYTES,
)

from build_search_shards import (
    MAX_BYTES,
    MAX_CATALOG_GZIP_BYTES,
    MAX_CATALOG_UNCOMPRESSED_BYTES,
    batch_fits,
    catalog_metadata,
    compact,
    encode_batch,
)


class SearchShardBatchBoundaryTests(unittest.TestCase):
    def test_builder_target_keeps_headroom_under_public_hard_cap(self):
        self.assertEqual(MAX_BYTES, 340_000)
        self.assertLess(MAX_BYTES, 350_000)

    def test_size_limit_counts_the_exact_trailing_newline(self):
        page = compact({
            "url": "https://example.com/search-boundary",
            "title": "Boundary",
            "description": "A deterministic shard-size fixture",
            "text": "short excerpt",
        })
        raw = encode_batch([page])

        self.assertTrue(raw.endswith(b"\n"))
        self.assertTrue(batch_fits([page], len(raw)))
        self.assertFalse(batch_fits([page], len(raw) - 1))

    def test_compressed_catalog_cap_supports_the_independent_search_backend(self):
        self.assertEqual(MAX_CATALOG_GZIP_BYTES, 16_000_000)

    def test_builder_and_validator_share_the_compressed_catalog_cap(self):
        self.assertEqual(MAX_CATALOG_GZIP_BYTES, VALIDATOR_MAX_CATALOG_GZIP_BYTES)
        self.assertEqual(MAX_CATALOG_GZIP_BYTES, 16_000_000)

    def test_compressed_catalog_at_current_observed_size_is_kept(self):
        observed_size = 10_113_629
        metadata = catalog_metadata(b"x" * observed_size, 21_024)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["bytes"], observed_size)

    def test_compressed_catalog_metadata_is_kept_within_the_serving_cap(self):
        raw = b"small compressed catalog"
        metadata = catalog_metadata(raw, 3)

        self.assertEqual(metadata["file"], "catalog-v1.json.gz")
        self.assertEqual(metadata["pages"], 3)
        self.assertEqual(metadata["bytes"], len(raw))
        self.assertEqual(len(metadata["sha256"]), 64)

    def test_compressed_catalog_metadata_is_omitted_above_the_serving_cap(self):
        raw = b"x" * (MAX_CATALOG_GZIP_BYTES + 1)
        self.assertIsNone(catalog_metadata(raw, 3))

    def test_builder_and_validator_share_the_uncompressed_catalog_cap(self):
        self.assertEqual(MAX_CATALOG_UNCOMPRESSED_BYTES, VALIDATOR_MAX_CATALOG_UNCOMPRESSED_BYTES)
        self.assertEqual(MAX_CATALOG_UNCOMPRESSED_BYTES, 64_000_000)

    def test_catalog_above_edge_budget_is_retained_for_vercel_fallback(self):
        # Large catalogs above the Edge Function's direct-load budget are delegated
        # to Vercel, whose bounded decoder allows up to 64 MB.
        metadata = catalog_metadata(b"small compressed catalog", 3, 30_234_899)
        self.assertIsNotNone(metadata)

    def test_compressed_catalog_is_omitted_above_the_uncompressed_safety_cap(self):
        self.assertIsNone(catalog_metadata(b"small compressed catalog", 3, MAX_CATALOG_UNCOMPRESSED_BYTES + 1))
        self.assertIsNotNone(catalog_metadata(b"small compressed catalog", 3, MAX_CATALOG_UNCOMPRESSED_BYTES))


    def test_compact_preserves_crawl_timestamps(self):
        page = compact({
            "url": "https://example.com/fresh",
            "title": "Fresh page",
            "fetched_at": 1794960000,
            "published_at": "2026-11-18T08:00:00Z",
        })
        self.assertEqual(page["fetched_at"], 1794960000)
        self.assertEqual(page["published_at"], "2026-11-18T08:00:00Z")

    def test_compact_omits_malformed_timestamps(self):
        page = compact({
            "url": "https://example.com/no-date",
            "fetched_at": "   ",
            "published_at": -10,
        })
        self.assertNotIn("fetched_at", page)
        self.assertNotIn("published_at", page)

    def test_builder_serialization_is_utf8_and_deterministic(self):
        page = compact({
            "url": "https://example.com/caf%C3%A9",
            "title": "Café discovery",
            "description": "Unicode stays intact",
        })
        first = encode_batch([page])
        second = encode_batch([page])

        self.assertEqual(first, second)
        self.assertIn("Café".encode("utf-8"), first)


if __name__ == "__main__":
    unittest.main()
