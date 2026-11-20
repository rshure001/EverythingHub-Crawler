import gzip
import json
import tempfile
import unittest
from pathlib import Path
from catalog_frontier import load_catalog_seed_urls

class CatalogFrontierTests(unittest.TestCase):
    def test_loads_only_unfetched_https_pages_and_deduplicates(self):
        payload={"source_pages":[
            {"url":"https://catalog.example/dataset/a","fetched":False,"policy_allowed":True},
            {"canonical_url":"https://catalog.example/dataset/a#section","fetched":False,"policy_allowed":True},
            {"url":"http://catalog.example/dataset/b","fetched":False,"policy_allowed":True},
            {"url":"https://catalog.example/dataset/c","fetched":True,"policy_allowed":True},
            {"url":"https://user:pass@catalog.example/dataset/d","fetched":False,"policy_allowed":True},
            {"url":"https://catalog.example/dataset/e","fetched":False,"policy_allowed":True},\n            {"url":"https://catalog.example/dataset/f","fetched":False}]}
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

if __name__ == "__main__":
    unittest.main()
