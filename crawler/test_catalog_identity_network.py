import unittest

from catalog_page_analysis import (
    _add_page_and_record_catalogs,
    analyze_pages,
    stable_id,
)


class PageAndRecordCatalogIdentityTests(unittest.TestCase):
    def test_every_valid_page_gets_a_distinct_catalog_in_full_analysis(self):
        result = analyze_pages(
            [
                {"url": "https://example.com/about", "title": "About Example", "robots_allowed": True},
                {"url": "https://example.com/contact", "title": "Contact Example", "robots_allowed": True},
            ],
            source_total=2,
        )
        self.assertEqual(result["metrics"]["page_catalogs"], 2)
        self.assertEqual(result["metrics"]["pages_with_page_catalogs"], 2)
        page_catalogs = [
            item for item in result["catalogs"]
            if item.get("catalog_origin") == "page_owned_identity_catalog"
        ]
        self.assertEqual(len(page_catalogs), 2)
        self.assertEqual(len({item["catalog_id"] for item in page_catalogs}), 2)
        self.assertTrue(all(len(item["source_page_ids"]) == 1 for item in page_catalogs))
        self.assertTrue(all(item["public_search_allowed"] is False for item in page_catalogs))

    def test_each_record_gets_its_own_catalog_and_page_membership(self):
        page_id = stable_id("page", "https://example.com/products")
        record_id = stable_id("record", "product|example.com|widget")
        pages = [{
            "page_id": page_id,
            "url": "https://example.com/products",
            "title": "Products",
            "record_ids": [record_id],
            "fetched_at": 1_700_000_000,
        }]
        records = {
            record_id: {
                "record_id": record_id,
                "record_type": "product",
                "title": "Widget",
                "source_url": "https://example.com/widget",
                "source_urls": ["https://example.com/widget"],
                "source_page_ids": [page_id],
                "catalog_ids": [],
                "last_seen_at": 1_700_000_000,
            }
        }
        catalogs = []
        metrics = _add_page_and_record_catalogs(pages, records, catalogs)

        self.assertEqual(metrics["page_catalogs"], 1)
        self.assertEqual(metrics["record_owned_catalogs"], 1)
        self.assertEqual(metrics["records_with_owned_catalogs"], 1)
        page_catalog_id = pages[0]["page_catalog_id"]
        record_catalog_id = records[record_id]["record_catalog_id"]
        self.assertNotEqual(page_catalog_id, record_catalog_id)
        self.assertIn(page_catalog_id, records[record_id]["catalog_ids"])
        self.assertIn(record_catalog_id, records[record_id]["catalog_ids"])
        by_id = {item["catalog_id"]: item for item in catalogs}
        self.assertEqual(by_id[page_catalog_id]["record_ids"], [record_id])
        self.assertEqual(by_id[record_catalog_id]["record_ids"], [record_id])
        self.assertEqual(by_id[record_catalog_id]["parent_catalog_id"], page_catalog_id)
        self.assertFalse(by_id[record_catalog_id]["public_search_allowed"])

    def test_empty_page_still_has_catalog_and_record_can_link_multiple_pages(self):
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
                "source_url": "https://example.com/story",
                "source_urls": ["https://example.com/story"],
                "source_page_ids": [page_a, page_b],
                "catalog_ids": [],
            }
        }
        catalogs = []
        metrics = _add_page_and_record_catalogs(pages, records, catalogs)
        by_id = {item["catalog_id"]: item for item in catalogs}
        self.assertEqual(metrics["page_catalogs"], 2)
        self.assertEqual(metrics["page_catalogs_without_records"], 1)
        self.assertEqual(by_id[pages[0]["page_catalog_id"]]["record_ids"], [])
        record_catalog = by_id[records[record_id]["record_catalog_id"]]
        self.assertIsNone(record_catalog["parent_catalog_id"])
        self.assertEqual(
            set(record_catalog["parent_page_catalog_ids"]),
            {pages[0]["page_catalog_id"], pages[1]["page_catalog_id"]},
        )


if __name__ == "__main__":
    unittest.main()
