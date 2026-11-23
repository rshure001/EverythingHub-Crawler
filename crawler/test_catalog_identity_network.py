import gzip
import json
import tempfile
import unittest
from pathlib import Path

from catalog_page_analysis import (
    _add_page_catalogs,
    _write_gzip_catalog_stream,
    analyze_pages,
    stable_id,
)


class PageAndRecordCatalogIdentityTests(unittest.TestCase):
    def test_every_valid_indexed_page_gets_one_dedicated_catalog(self):
        result = analyze_pages(
            [
                {"url": "https://example.com/about", "title": "About Example", "robots_allowed": True},
                {"url": "https://example.com/contact", "title": "Contact Example", "robots_allowed": True},
            ],
            source_total=2,
        )
        self.assertEqual(result["metrics"]["page_catalogs"], 2)
        self.assertEqual(result["metrics"]["pages_with_page_catalogs"], 2)
        page_catalogs = result["page_catalogs"]
        self.assertEqual(len(page_catalogs), 2)
        self.assertEqual(len({item["catalog_id"] for item in page_catalogs}), 2)
        self.assertTrue(all(len(item["source_page_ids"]) == 1 for item in page_catalogs))
        self.assertTrue(all(item["public_search_allowed"] is False for item in page_catalogs))
        self.assertEqual(
            {item["catalog_id"] for item in page_catalogs},
            {page["page_catalog_id"] for page in result["pages"]},
        )

    def test_record_keeps_its_own_catalog_id_and_links_to_its_page_catalog(self):
        page_id = stable_id("page", "https://example.com/products")
        record_id = stable_id("record", "product|example.com|widget")
        pages = [{
            "page_id": page_id, "url": "https://example.com/products",
            "title": "Products", "record_ids": [record_id],
        }]
        record_catalog_id = stable_id("record-catalog", record_id)
        records = {
            record_id: {
                "record_id": record_id,
                "record_type": "product",
                "title": "Widget",
                "record_catalog_id": record_catalog_id,
                "source_page_ids": [page_id],
                "catalog_ids": [],
            }
        }
        metrics, page_catalogs, record_catalogs = _add_page_catalogs(pages, records)
        self.assertEqual(metrics["page_catalogs"], 1)
        self.assertEqual(records[record_id]["record_catalog_id"], record_catalog_id)
        self.assertEqual(records[record_id]["page_catalog_ids"], [pages[0]["page_catalog_id"]])
        self.assertEqual(page_catalogs[0]["record_ids"], [record_id])
        self.assertEqual(record_catalogs[0]["catalog_id"], record_catalog_id)
        self.assertEqual(record_catalogs[0]["record_ids"], [record_id])
        self.assertNotEqual(record_catalog_id, pages[0]["page_catalog_id"])
        self.assertFalse(page_catalogs[0]["public_search_allowed"])

    def test_streamed_record_catalogs_write_valid_json_without_materializing_a_list(self):
        page_id = stable_id("page", "https://example.com/stream")
        record_id = stable_id("record", "article|example.com|streamed")
        pages = [{
            "page_id": page_id,
            "url": "https://example.com/stream",
            "title": "Streamed page",
            "record_ids": [record_id],
        }]
        records = {
            record_id: {
                "record_id": record_id,
                "record_type": "article",
                "title": "Streamed record",
                "record_catalog_id": stable_id("record-catalog", record_id),
                "source_page_ids": [page_id],
                "catalog_ids": [],
            }
        }
        metrics, _, catalog_stream = _add_page_catalogs(
            pages, records, stream_record_catalogs=True
        )
        self.assertFalse(isinstance(catalog_stream, list))
        self.assertEqual(metrics["record_owned_catalogs"], 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "record-catalogs.json.gz"
            size, digest, count = _write_gzip_catalog_stream(path, catalog_stream)
            payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        self.assertGreater(size, 0)
        self.assertEqual(len(digest), 64)
        self.assertEqual(count, 1)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["catalogs"][0]["record_id"], record_id)
        self.assertEqual(
            payload["catalogs"][0]["page_catalog_ids"],
            [pages[0]["page_catalog_id"]],
        )

    def test_empty_page_gets_catalog_and_shared_record_links_all_source_pages(self):
        page_a = stable_id("page", "https://example.com/a")
        page_b = stable_id("page", "https://example.com/b")
        record_id = stable_id("record", "article|example.com|story")
        pages = [
            {"page_id": page_a, "url": "https://example.com/a", "title": "A", "record_ids": []},
            {"page_id": page_b, "url": "https://example.com/b", "title": "B", "record_ids": [record_id]},
        ]
        records = {
            record_id: {
                "record_id": record_id,
                "record_type": "article",
                "title": "Story",
                "record_catalog_id": stable_id("record-catalog", record_id),
                "source_page_ids": [page_a, page_b],
                "catalog_ids": [],
            }
        }
        metrics, page_catalogs, record_catalogs = _add_page_catalogs(pages, records)
        by_id = {item["catalog_id"]: item for item in page_catalogs}
        self.assertEqual(metrics["page_catalogs"], 2)
        self.assertEqual(metrics["page_catalogs_without_records"], 1)
        self.assertEqual(by_id[pages[0]["page_catalog_id"]]["record_ids"], [])
        self.assertEqual(
            set(records[record_id]["page_catalog_ids"]),
            {pages[0]["page_catalog_id"], pages[1]["page_catalog_id"]},
        )
        self.assertEqual(len(record_catalogs), 1)
        self.assertEqual(record_catalogs[0]["page_catalog_ids"], sorted(records[record_id]["page_catalog_ids"]))


if __name__ == "__main__":
    unittest.main()
