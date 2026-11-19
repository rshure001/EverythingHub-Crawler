import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from build_search_shards import compact, is_publishable
from lane_classifier import VALID_LANES, classify_lane
from validate_public_index import validate


class LaneClassifierTests(unittest.TestCase):
    def test_public_index_excludes_explicit_robots_denials(self):
        self.assertFalse(is_publishable({"url": "https://example.org/private", "robots_allowed": False}))

    def test_public_index_keeps_records_without_legacy_robots_flag(self):
        self.assertTrue(is_publishable({"url": "https://example.org/public"}))

    def test_public_index_keeps_records_with_explicit_robots_allow(self):
        self.assertTrue(is_publishable({"url": "https://example.org/public", "robots_allowed": True}))

    def test_public_index_rejects_malformed_urls(self):
        self.assertFalse(is_publishable({"url": None}))
        self.assertFalse(is_publishable({"url": "javascript:alert(1)"}))

    def test_all_eight_lanes_have_curated_source_examples(self):
        examples = {
            "general": "https://www.wikipedia.org/",
            "science": "https://www.nasa.gov/",
            "news": "https://www.reuters.com/",
            "technology": "https://ocw.mit.edu/",
            "business": "https://www.investopedia.com/",
            "government": "https://www.irs.gov/",
            "culture": "https://www.loc.gov/",
            "nature": "https://www.nationalgeographic.com/",
        }
        self.assertEqual(set(examples), set(VALID_LANES))
        for expected, url in examples.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify_lane(url), expected)

    def test_subdomains_match_but_lookalike_suffixes_do_not(self):
        self.assertEqual(classify_lane("https://science.nasa.gov/"), "science")
        self.assertEqual(classify_lane("https://nasa.gov.example.com/"), "general")

    def test_unknown_and_invalid_hosts_default_to_general(self):
        self.assertEqual(classify_lane("https://unknown.example/"), "general")
        self.assertEqual(classify_lane("not a URL"), "general")

    def test_public_shard_compaction_always_emits_a_valid_lane(self):
        page = compact({
            "url": "https://www.nasa.gov/",
            "title": "NASA",
            "description": "Science",
            "text": "Space",
        })
        self.assertEqual(page["lane"], "science")
        self.assertIn(page["lane"], VALID_LANES)

    def test_public_shard_compaction_preserves_a_valid_lane(self):
        page = compact({"url": "https://www.nasa.gov/", "lane": "news"})
        self.assertEqual(page["lane"], "news")

    def _validator_errors(self, lane):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "public-search").mkdir()
            url = "https://www.nasa.gov/"
            (root / "data" / "index.json").write_text(
                json.dumps({"updated_at": 1700000000, "pages": [{"url": url}]}), encoding="utf-8"
            )
            page = {"url": url, "title": "NASA", "description": "", "text": "", "lane": lane}
            raw = json.dumps(
                {"version": 1, "pages": [page]},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            shard_name = "shard-0001.json"
            (root / "public-search" / shard_name).write_bytes(raw)
            manifest = {
                "version": 1,
                "integrity": "sha256",
                "updated_at": 1700000000,
                "total_pages": 1,
                "shard_count": 1,
                "shards": [{
                    "file": shard_name,
                    "pages": 1,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }],
            }
            (root / "public-search" / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            return validate(root)

    def test_validator_accepts_a_lane_aware_shard(self):
        self.assertEqual(self._validator_errors("science"), [])

    def test_validator_rejects_an_unknown_lane(self):
        errors = self._validator_errors("unmapped")
        self.assertTrue(any("invalid search lane" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
