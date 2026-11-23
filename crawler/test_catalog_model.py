import unittest

from catalog_model import catalog_metrics, normalize_catalog, normalize_record, query_catalogs, stable_id


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
        self.assertEqual(self.catalog["catalog_level"], "root")
        self.assertEqual(self.catalog["parent_catalog_id"], "")

    def test_record_belongs_to_catalog_and_has_provenance(self):
        record = normalize_record({
            "source_key": "submariner-126610",
            "title": "Submariner Date",
            "record_type": "model",
            "source_url": "https://www.rolex.com/watches/submariner",
            "attributes": {"reference": "126610"},
        }, self.catalog["catalog_id"])
        self.assertEqual(record["catalog_id"], self.catalog["catalog_id"])
        self.assertEqual(record["catalog_ids"], [self.catalog["catalog_id"]])
        self.assertEqual(record["source_urls"], ["https://www.rolex.com/watches/submariner"])
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
            "catalogs_discovered": 1, "subcatalogs_discovered": 0,
            "catalog_records": 2, "record_catalog_memberships": 0,
            "unique_entities": 1, "source_page_references": 2,
            "source_page_associations": 3, "source_pages_fetched": 0,
            "indexed_records": 1,
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

    def test_canonical_record_id_is_stable_across_catalog_memberships(self):
        raw = {
            "source_key": "OL123W",
            "title": "A Science Book",
            "record_type": "document",
            "source_url": "https://openlibrary.org/works/OL123W",
            "canonical_entity_key": "openlibrary:work:OL123W",
        }
        first = normalize_record(raw, "catalog:science")
        second = normalize_record(raw, "catalog:history")
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(first["catalog_ids"], ["catalog:science"])
        self.assertEqual(second["catalog_ids"], ["catalog:history"])

    def test_subcatalog_requires_a_distinct_parent(self):
        with self.assertRaisesRegex(ValueError, "require parent_catalog_id"):
            normalize_catalog({
                "source_id": "orphan-subcatalog",
                "title": "Orphan",
                "catalog_type": "open_data",
                "source_url": "https://example.org/catalog",
                "catalog_level": "subcatalog",
            })
        self_id = stable_id("catalog", "self-parent")
        with self.assertRaisesRegex(ValueError, "cannot be its own parent"):
            normalize_catalog({
                "source_id": "self-parent",
                "title": "Self",
                "catalog_type": "open_data",
                "source_url": "https://example.org/catalog",
                "catalog_level": "subcatalog",
                "parent_catalog_id": self_id,
            })

    def test_rejects_non_https_source(self):
        with self.assertRaises(ValueError):
            normalize_catalog({
                "source_id": "bad", "title": "Bad", "catalog_type": "general",
                "source_url": "http://example.org",
            })


if __name__ == "__main__":
    unittest.main()
