import unittest

from build_search_shards import batch_fits, compact, encode_batch


class SearchShardBatchBoundaryTests(unittest.TestCase):
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
