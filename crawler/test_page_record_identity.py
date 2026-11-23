import unittest

from catalog_page_analysis import analyze_pages, stable_id


class PageRecordIdentityTests(unittest.TestCase):
    def test_source_page_without_catalog_evidence_remains_only_a_page(self):
        result = analyze_pages(
            [{
                "url": "https://example.com/about",
                "title": "About Example",
                "description": "",
                "text": "",
                "robots_allowed": True,
            }],
            source_total=1,
        )
        self.assertEqual(len(result["pages"]), 1)
        self.assertEqual(result["catalogs"], [])
        self.assertEqual(result["metrics"]["unique_catalogs_discovered"], 0)
        self.assertNotIn("page_catalogs", result)
        self.assertNotIn("record_catalogs", result)
        page = result["pages"][0]
        self.assertEqual(page["page_id"], stable_id("page", page["url"]))
        self.assertNotIn("page_catalog_id", page)
        self.assertNotIn("record_catalog_ids", page)

    def test_records_use_canonical_record_ids_and_real_catalog_memberships(self):
        result = analyze_pages(
            [
                {
                    "url": "https://www.rolex.com/watches/submariner",
                    "title": "Submariner",
                    "description": "Rolex watch model",
                    "text": "Discover the Rolex Submariner watch collection and its features.",
                    "lane": "shopping",
                    "robots_allowed": True,
                },
                {
                    "url": "https://www.rolex.com/watches/day-date",
                    "title": "Day-Date",
                    "description": "Rolex watch model",
                    "text": "Explore the Rolex Day-Date watch and its details.",
                    "lane": "shopping",
                    "robots_allowed": True,
                },
            ],
            source_total=2,
        )
        records = {record["record_id"]: record for record in result["records"]}
        self.assertTrue(records)
        real_catalog_ids = {catalog["catalog_id"] for catalog in result["catalogs"]}
        for record in records.values():
            self.assertEqual(
                record["record_id"],
                stable_id("record", record["canonical_entity_key"]),
            )
            self.assertNotIn("record_catalog_id", record)
            self.assertNotIn("page_catalog_ids", record)
            self.assertTrue(record["source_page_ids"])
            self.assertTrue(set(record.get("catalog_ids", [])).issubset(real_catalog_ids))
        for page in result["pages"]:
            self.assertNotIn("page_catalog_id", page)
            self.assertNotIn("record_catalog_ids", page)
            for record_id in page["record_ids"]:
                self.assertIn(page["page_id"], records[record_id]["source_page_ids"])


if __name__ == "__main__":
    unittest.main()
