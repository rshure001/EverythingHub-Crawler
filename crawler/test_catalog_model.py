import unittest

from catalog_model import catalog_metrics, normalize_catalog, normalize_record, query_catalogs


class UniversalCatalogModelTests(unittest.TestCase):
    def setUp(self):
        self.catalog = normalize_catalog({
            "source_id": "rolex-models",
            "title": "Rolex Watch Models",
            "description": "Model and reference collection",
            "catalog_type": "brand",
            "subject": "watches",
            "publisher": "Rolex",
            "source_url": "https://www.rolex.com/watches",
            "coverage_status": "partial",
            "record_count": 2,
        })

    def test_catalog_is_a_collection_not_a_page(self):
        self.assertEqual(self.catalog["catalog_type"], "brand")
        self.assertEqual(self.catalog["coverage_status"], "partial")
        self.assertEqual(self.catalog["record_count"], 2)
        self.assertTrue(self.catalog["catalog_id"].startswith("catalog:"))

    def test_record_belongs_to_catalog_and_has_provenance(self):
        record = normalize_record({
            "source_key": "submariner-126610",
            "title": "Submariner Date",
            "record_type": "model",
            "source_url": "https://www.rolex.com/watches/submariner",
            "attributes": {"reference": "126610"},
        }, self.catalog["catalog_id"])
        self.assertEqual(record["catalog_id"], self.catalog["catalog_id"])
        self.assertEqual(record["attributes"]["reference"], "126610")

    def test_counts_keep_catalog_records_entities_and_pages_separate(self):
        r1 = {"record_id": "r1", "canonical_entity_key": "watch:x", "indexed": True}
        r2 = {"record_id": "r2", "canonical_entity_key": "watch:x", "indexed": False}
        metrics = catalog_metrics(
            [self.catalog], [r1, r2],
            [{"url": "https://example.org/a"}, {"url": "https://example.org/a"},
             {"url": "https://example.org/b"}],
        )
        self.assertEqual(metrics, {
            "catalogs_discovered": 1, "catalog_records": 2,
            "unique_entities": 1, "source_page_references": 2,
            "source_pages_fetched": 0, "indexed_records": 1,
        })

    def test_query_finds_brand_and_geographic_catalogs(self):
        catalogs = [
            self.catalog,
            normalize_catalog({
                "source_id": "las-vegas-businesses", "title": "Las Vegas Business Directory",
                "catalog_type": "business_directory", "geography": "Las Vegas, Nevada",
                "source_url": "https://example.org/las-vegas",
            }),
        ]
        self.assertEqual(query_catalogs("Rolex", catalogs)[0]["source_id"], "rolex-models")
        self.assertEqual(query_catalogs("Las Vegas", catalogs)[0]["source_id"], "las-vegas-businesses")

    def test_rejects_non_https_source(self):
        with self.assertRaises(ValueError):
            normalize_catalog({
                "source_id": "bad", "title": "Bad", "catalog_type": "general",
                "source_url": "http://example.org",
            })


if __name__ == "__main__":
    unittest.main()
