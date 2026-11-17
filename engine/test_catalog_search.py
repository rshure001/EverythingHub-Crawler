import gzip
import json
import tempfile
import unittest
from pathlib import Path

from catalog_search import load_catalog


class CatalogLoaderTests(unittest.TestCase):
    def test_loads_version_one_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"version": 1, "pages": [{"url": "https://example.com"}]}, stream)
            self.assertEqual(load_catalog(path), [{"url": "https://example.com"}])

    def test_rejects_unknown_catalog_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"version": 2, "pages": []}, stream)
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_rejects_non_list_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"version": 1, "pages": {}}, stream)
            with self.assertRaises(ValueError):
                load_catalog(path)


if __name__ == "__main__":
    unittest.main()
