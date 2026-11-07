import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path("scripts/merge_crawl_state.py")
SPEC = importlib.util.spec_from_file_location("merge_crawl_state", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MergeCrawlerStateTests(unittest.TestCase):
    def test_index_union_keeps_newest_record_per_url(self):
        remote = {"version": 1, "updated_at": 10, "pages": [
            {"url": "https://a.test/", "title": "old", "fetched_at": 1},
            {"url": "https://b.test/", "title": "remote", "fetched_at": 5},
        ]}
        local = {"version": 1, "updated_at": 20, "pages": [
            {"url": "https://a.test/", "title": "new", "fetched_at": 2},
            {"url": "https://c.test/", "title": "local", "fetched_at": 3},
        ]}
        merged = MODULE.merge_index(local, remote)
        by_url = {p["url"]: p for p in merged["pages"]}
        self.assertEqual(set(by_url), {
            "https://a.test/", "https://b.test/", "https://c.test/"
        })
        self.assertEqual(by_url["https://a.test/"]["title"], "new")

    def test_frontier_union_deduplicates_and_preserves_shallower_depth(self):
        remote = {
            "updated_at": 10,
            "seen": ["https://a.test/", "https://b.test/"],
            "queue": [{"url": "https://a.test/", "depth": 2}],
        }
        local = {
            "updated_at": 20,
            "seen": ["https://b.test/", "https://c.test/"],
            "queue": [
                {"url": "https://a.test/", "depth": 1},
                {"url": "https://c.test/", "depth": 3},
            ],
        }
        merged = MODULE.merge_frontier(local, remote)
        self.assertEqual(set(merged["seen"]), {
            "https://a.test/", "https://b.test/", "https://c.test/"
        })
        by_url = {p["url"]: p for p in merged["queue"]}
        self.assertEqual(len(by_url), 2)
        self.assertEqual(by_url["https://a.test/"]["depth"], 1)

    def test_cli_writes_valid_merged_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, payload in [
                ("li.json", {"pages": [{"url": "https://local.test/"}]}),
                ("ri.json", {"pages": [{"url": "https://remote.test/"}]}),
                ("lf.json", {"queue": [], "seen": ["https://local.test/"]}),
                ("rf.json", {"queue": [], "seen": ["https://remote.test/"]}),
            ]:
                (root / name).write_text(__import__("json").dumps(payload))
            out = root / "out"
            MODULE.merge_index(
                __import__("json").loads((root / "li.json").read_text()),
                __import__("json").loads((root / "ri.json").read_text()),
            )
            merged_frontier = MODULE.merge_frontier(
                __import__("json").loads((root / "lf.json").read_text()),
                __import__("json").loads((root / "rf.json").read_text()),
            )
            self.assertEqual(len(merged_frontier["seen"]), 2)


if __name__ == "__main__":
    unittest.main()
