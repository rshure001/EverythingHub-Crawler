import gzip
import json
import tempfile
import unittest
from pathlib import Path
from catalog_frontier import load_catalog_seed_urls, plan_catalog_seed_enqueue

class CatalogFrontierTests(unittest.TestCase):
    def test_loads_only_unfetched_https_pages_and_deduplicates(self):
        payload={"source_pages":[
            {"url":"https://catalog.example/dataset/a","fetched":False,"policy_allowed":True},
            {"canonical_url":"https://catalog.example/dataset/a#section","fetched":False,"policy_allowed":True},
            {"url":"http://catalog.example/dataset/b","fetched":False,"policy_allowed":True},
            {"url":"https://catalog.example/dataset/c","fetched":True,"policy_allowed":True},
            {"url":"https://user:pass@catalog.example/dataset/d","fetched":False,"policy_allowed":True},
            {"url":"https://catalog.example/dataset/e","fetched":False,"policy_allowed":True},
            {"url":"https://catalog.example/dataset/f","fetched":False}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"catalog.json.gz"
            path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
            self.assertEqual(load_catalog_seed_urls(path),[
                "https://catalog.example/dataset/a","https://catalog.example/dataset/e"])

    def test_respects_limit_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"catalog.json"
            path.write_text(json.dumps({"source_pages":[
                {"url":"https://a.example/1","fetched":False,"policy_allowed":True},
                {"url":"https://b.example/2","fetched":False,"policy_allowed":True}]}))
            self.assertEqual(len(load_catalog_seed_urls(path,limit=1)),1)
            self.assertEqual(load_catalog_seed_urls(path.with_name("missing.json")),[])

    def test_rejects_negative_limit(self):
        with self.assertRaises(ValueError):
            load_catalog_seed_urls(Path("missing.json"),limit=-1)


    def test_plans_catalog_seed_additions_and_explains_skips(self):
        seeds = [
            "https://catalog.example/dataset/indexed",
            "https://catalog.example/dataset/queued",
            "https://catalog.example/dataset/seen",
            "https://catalog.example/dataset/new",
            "https://catalog.example/dataset/deferred",
            "https://catalog.example/dataset/deferred",
        ]
        additions, metrics = plan_catalog_seed_enqueue(
            seeds,
            indexed_urls={"https://catalog.example/dataset/indexed"},
            seen_urls={
                "https://catalog.example/dataset/queued",
                "https://catalog.example/dataset/seen",
            },
            queued_urls={"https://catalog.example/dataset/queued"},
            queue_remaining=1,
        )
        self.assertEqual(additions, ["https://catalog.example/dataset/new"])
        self.assertEqual(metrics, {
            "candidates": 5,
            "duplicates": 1,
            "already_indexed": 1,
            "already_queued": 1,
            "already_seen": 1,
            "queue_full": 1,
            "queued": 1,
        })

    def test_catalog_seed_plan_handles_full_queue_and_rejects_negative_capacity(self):
        additions, metrics = plan_catalog_seed_enqueue(
            ["https://catalog.example/dataset/new"],
            indexed_urls=set(), seen_urls=set(), queued_urls=set(), queue_remaining=0,
        )
        self.assertEqual(additions, [])
        self.assertEqual(metrics["queue_full"], 1)
        self.assertEqual(metrics["queued"], 0)
        with self.assertRaises(ValueError):
            plan_catalog_seed_enqueue(
                [], indexed_urls=set(), seen_urls=set(), queued_urls=set(), queue_remaining=-1,
            )

if __name__ == "__main__":
    unittest.main()
