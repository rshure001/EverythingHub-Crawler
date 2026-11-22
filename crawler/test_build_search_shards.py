import gzip
import json
import tempfile
import unittest
from pathlib import Path

import build_search_shards as builder_module
import validate_public_index as validator_module

from validate_public_index import (
    MAX_CATALOG_GZIP_BYTES as VALIDATOR_MAX_CATALOG_GZIP_BYTES,
    MAX_CATALOG_UNCOMPRESSED_BYTES as VALIDATOR_MAX_CATALOG_UNCOMPRESSED_BYTES,
    MAX_SHARD_COUNT as VALIDATOR_MAX_SHARD_COUNT,
    MAX_TOTAL_COMPRESSED_SHARD_BYTES,
    MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES,
)

from build_search_shards import (
    MAX_BYTES,
    MAX_COMPRESSED_BYTES,
    MAX_SHARD_COUNT,
    SHARD_GZIP_LEVEL,
    MAX_CATALOG_GZIP_BYTES,
    MAX_CATALOG_UNCOMPRESSED_BYTES,
    batch_fits,
    batch_size_from_item_sizes,
    encoded_page_size,
    catalog_metadata,
    compact,
    encode_batch,
    encode_shard,
    build_query_index,
    query_tokens,
)


class SearchShardBatchBoundaryTests(unittest.TestCase):
    def test_builder_target_keeps_headroom_under_public_hard_cap(self):
        self.assertEqual(MAX_BYTES, 500_000)
        self.assertEqual(MAX_COMPRESSED_BYTES, 350_000)
        self.assertLess(MAX_COMPRESSED_BYTES, 1_000_000)
        self.assertEqual(SHARD_GZIP_LEVEL, 6)

    def test_shard_capacity_is_aligned_across_builder_and_validator(self):
        self.assertEqual(MAX_SHARD_COUNT, 768)
        self.assertEqual(MAX_SHARD_COUNT, VALIDATOR_MAX_SHARD_COUNT)
        self.assertEqual(MAX_TOTAL_COMPRESSED_SHARD_BYTES, 100_000_000)
        self.assertEqual(MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES, 400_000_000)

    def test_incremental_size_accounting_matches_exact_utf8_batch_bytes(self):
        pages = [
            compact({
                "url": "https://example.com/one",
                "title": "Café",
                "description": "first",
            }),
            compact({
                "url": "https://example.com/two",
                "title": "Second",
                "text": "🛰️",
            }),
        ]
        sizes = [encoded_page_size(page) for page in pages]
        self.assertEqual(batch_size_from_item_sizes(sizes), len(encode_batch(pages)))
        self.assertEqual(
            batch_size_from_item_sizes([sizes[0]]),
            len(encode_batch([pages[0]])),
        )
        self.assertEqual(batch_size_from_item_sizes([]), len(encode_batch([])))

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

    def test_public_shards_are_deterministic_gzip_with_exact_uncompressed_size(self):
        page = compact({"url": "https://example.com/compressed", "title": "Compressed"})
        compressed, uncompressed_size = encode_shard([page])
        expected = encode_batch([page])
        self.assertEqual(gzip.decompress(compressed), expected)
        self.assertEqual(uncompressed_size, len(expected))
        self.assertEqual(compressed, encode_shard([page])[0])
        self.assertLessEqual(len(compressed), MAX_COMPRESSED_BYTES)

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


    def test_compact_preserves_structured_product_fields(self):
        page = compact({
            "url": "https://shop.example/item-1",
            "title": "Diamond ring",
            "product": {
                "type": "product", "name": "18K Diamond Pinky Ring",
                "brand": "Example Jewelers", "sku": "RING-18K",
                "price": "1299.00", "currency": "USD",
                "availability": "InStock",
                "image": "https://shop.example/ring.jpg",
                "url": "https://shop.example/item-1",
            },
        })
        self.assertEqual(page["product"]["name"], "18K Diamond Pinky Ring")
        self.assertEqual(page["product"]["price"], "1299.00")
        self.assertEqual(page["product"]["image"], "https://shop.example/ring.jpg")

    def test_compact_omits_unsafe_product_image_and_url(self):
        page = compact({
            "url": "https://shop.example/item-3",
            "product": {
                "type": "product", "name": "Ring",
                "image": "javascript:alert(1)",
                "url": "https://user:secret@shop.example/item-3",
            },
        })
        self.assertNotIn("image", page["product"])
        self.assertNotIn("url", page["product"])

    def test_compact_ignores_malformed_product_metadata(self):
        page = compact({
            "url": "https://shop.example/item-2",
            "product": {"type": "product", "brand": "No product name"},
        })
        self.assertNotIn("product", page)

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

    def test_query_tokens_cover_page_and_product_fields_with_unicode_normalization(self):
        page = compact({
            "url": "https://example.com/las-vegas",
            "title": "ＮＡＳＡ Las Vegas",
            "description": "The Space Center",
            "text": "Rolex model 5 collection",
            "product": {
                "type": "product", "name": "Explorer Watch",
                "brand": "Rolex", "sku": "RX-42",
                "price": "1299.00", "availability": "InStock",
            },
        })
        tokens = query_tokens(page)
        self.assertTrue({"nasa", "las", "vegas", "space", "center", "rolex", "explorer", "rx", "42", "5"}.issubset(tokens))
        self.assertNotIn("the", tokens)

    def test_query_index_is_deterministic_and_tracks_distinct_page_counts(self):
        postings = {
            "nasa": {1: 2, 0: 3},
            "rolex": {1: 1},
        }
        first = build_query_index(postings)
        second = build_query_index(postings)
        self.assertEqual(first, second)
        prefix = __import__("hashlib").sha256(b"nasa").hexdigest()[:2]
        self.assertEqual(
            first[prefix]["tokens"]["nasa"],
            [[0, 3], [1, 2]],
        )

    def test_builder_and_validator_round_trip_query_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            data_dir.mkdir()
            source = {
                "updated_at": 1795347352,
                "pages": [
                    {
                        "url": "https://www.nasa.gov/missions/mars",
                        "title": "NASA Mars Missions",
                        "description": "Space science exploration",
                        "text": "Mars mission directory",
                        "lane": "science",
                        "robots_allowed": True,
                    },
                    {
                        "url": "https://www.rolex.com/watches",
                        "title": "Rolex Watches",
                        "description": "Swiss watchmaking",
                        "text": "Luxury timepieces",
                        "lane": "business",
                        "robots_allowed": True,
                    },
                    {
                        "url": "https://www.lasvegasnevada.gov/places",
                        "title": "Las Vegas Places",
                        "description": "City places and destinations",
                        "text": "Public places directory",
                        "lane": "government",
                        "robots_allowed": True,
                    },
                ],
            }
            source_path = data_dir / "index.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            original_source = builder_module.SOURCE
            original_out = builder_module.OUT
            original_root = validator_module.ROOT
            try:
                builder_module.SOURCE = source_path
                builder_module.OUT = root / "public-search"
                validator_module.ROOT = root
                builder_module.main()
                errors = validator_module.validate(root)
                self.assertEqual(errors, [])
                manifest = json.loads((root / "public-search" / "manifest.json").read_text(encoding="utf-8"))
                query_index = manifest["query_index"]
                self.assertGreater(query_index["token_count"], 0)
                self.assertGreater(query_index["posting_count"], 0)
                self.assertEqual(query_index["shard_count"], len(query_index["shards"]))
            finally:
                builder_module.SOURCE = original_source
                builder_module.OUT = original_out
                validator_module.ROOT = original_root

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
