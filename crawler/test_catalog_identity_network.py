import unittest

from catalog_page_analysis import _add_page_catalogs, analyze_pages, stable_id


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

    def test_empty_page_gets_catalog_and_shared_record_links_all_source_pages(self):
        page_a = stable_id("page", "https://example.com/a")
        page_b = stable_id("page", "https://example.com/b")
        record_id = stable_id("record", "article|example.com|story")
        page_c = stable_id("page", "https://example.com/c")
        pages = [
            {"page_id": page_a, "url": "https://example.com/a", "title": "A", "record_ids": []},
            {"page_id": page_b, "url": "https://example.com/b", "title": "B", "record_ids": [record_id]},
            {"page_id": page_c, "url": "https://example.com/c", "title": "C", "record_ids": []},
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
        self.assertEqual(metrics["page_catalogs"], 3)
        self.assertEqual(metrics["page_catalogs_without_records"], 1)
        self.assertEqual(by_id[pages[0]["page_catalog_id"]]["record_ids"], [record_id])
        self.assertEqual(by_id[pages[2]["page_catalog_id"]]["record_ids"], [])
        self.assertEqual(
            set(records[record_id]["page_catalog_ids"]),
            {pages[0]["page_catalog_id"], pages[1]["page_catalog_id"]},
        )
        self.assertEqual(len(record_catalogs), 1)
        self.assertEqual(record_catalogs[0]["page_catalog_ids"], sorted(records[record_id]["page_catalog_ids"]))


if __name__ == "__main__":
    unittest.main()
