import unittest

from crawler.record_related_catalogs import (
    build_related_catalog_definition,
    resolve_related_records,
    stable_record_catalog_id,
    validate_record_catalog_inputs,
)


class RecordRelatedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.records = {
            "r1": {"record_id": "r1", "title": "Owner", "source_page_ids": ["p1"], "source_urls": ["https://a.test/1"], "rights_status": "restricted"},
            "r2": {"record_id": "r2", "title": "Two", "source_page_ids": ["p2"], "source_urls": ["https://a.test/2"]},
            "r3": {"record_id": "r3", "title": "Three", "source_page_ids": ["p3"], "source_urls": ["https://b.test/3"]},
            "r4": {"record_id": "r4", "title": "Four", "source_page_ids": ["p4"], "source_urls": ["https://c.test/4"]},
        }
        self.catalogs = {
            "c1": {"catalog_id": "c1", "record_ids": ["r1", "r2", "r3"]},
            "c2": {"catalog_id": "c2", "record_ids": ["r1", "r2", "r4"]},
        }

    def test_stable_id(self):
        self.assertEqual(stable_record_catalog_id("r1"), stable_record_catalog_id("r1"))
        self.assertNotEqual(stable_record_catalog_id("r1"), stable_record_catalog_id("r2"))

    def test_definition_uses_compact_refs_and_preserves_provenance(self):
        definition = build_related_catalog_definition({
            "record_id": "r1", "catalog_ids": ["c2", "c1", "c1"],
            "source_page_ids": ["p1"], "source_urls": ["https://a.test/1"],
            "rights_status": "restricted", "last_seen_at": "2026-11-24T12:00:00Z",
        })
        self.assertEqual(definition["shared_catalog_ids"], ["c1", "c2"])
        self.assertEqual(definition["rights_status"], "restricted")
        self.assertEqual(definition["source_page_ids"], ["p1"])
        self.assertEqual(definition["membership_mode"], "virtual_shared_catalog_references")
        self.assertNotIn("record_ids", definition)

    def test_related_items_exclude_owner_and_deduplicate(self):
        definition = build_related_catalog_definition({"record_id": "r1", "catalog_ids": ["c1", "c2"]})
        result = resolve_related_records("r1", definition, self.catalogs, self.records)
        self.assertEqual([item["record_id"] for item in result["items"]], ["r2", "r3", "r4"])
        self.assertNotIn("r1", [item["record_id"] for item in result["items"]])

    def test_pagination_is_stable(self):
        definition = build_related_catalog_definition({"record_id": "r1", "catalog_ids": ["c1", "c2"]})
        first = resolve_related_records("r1", definition, self.catalogs, self.records, limit=2)
        second = resolve_related_records("r1", definition, self.catalogs, self.records, offset=2, limit=2)
        self.assertEqual([x["record_id"] for x in first["items"]], ["r2", "r3"])
        self.assertTrue(first["has_more"])
        self.assertEqual([x["record_id"] for x in second["items"]], ["r4"])
        self.assertFalse(second["has_more"])

    def test_empty_related_catalog_returns_honest_empty_page(self):
        definition = build_related_catalog_definition({"record_id": "r1", "catalog_ids": []})
        result = resolve_related_records("r1", definition, self.catalogs, self.records)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["membership_mode"], "virtual_shared_catalog_references")

    def test_resolver_rejects_catalog_owned_by_another_record(self):
        definition = build_related_catalog_definition({"record_id": "r2", "catalog_ids": ["c1"]})
        with self.assertRaisesRegex(ValueError, "owner does not match"):
            resolve_related_records("r1", definition, self.catalogs, self.records)

    def test_resolver_rejects_boolean_pagination_values(self):
        definition = build_related_catalog_definition({"record_id": "r1", "catalog_ids": ["c1"]})
        with self.assertRaisesRegex(ValueError, "offset must be a non-negative integer"):
            resolve_related_records("r1", definition, self.catalogs, self.records, offset=True)
        with self.assertRaisesRegex(ValueError, "limit must be an integer"):
            resolve_related_records("r1", definition, self.catalogs, self.records, limit=True)

    def test_unsorted_legacy_catalogs_are_normalized(self):
        catalogs = {"c": {"record_ids": ["r3", "r1", "r2", "r2"]}}
        definition = build_related_catalog_definition({"record_id": "r1", "catalog_ids": ["c"]})
        result = resolve_related_records("r1", definition, catalogs, self.records)
        self.assertEqual([x["record_id"] for x in result["items"]], ["r2", "r3"])

    def test_invalid_record_id_rejected(self):
        with self.assertRaises(ValueError):
            build_related_catalog_definition({"record_id": ""})


    def test_registry_validation_counts_stable_catalogs_and_shared_refs(self):
        records = {
            "r1": {"record_id": "r1", "catalog_ids": ["c1", "c2"], "source_urls": ["https://a.test/1"], "source_page_ids": ["p1"]},
            "r2": {"record_id": "r2", "catalog_ids": ["c1"], "source_urls": ["https://a.test/2"], "source_page_ids": ["p2"]},
            "r3": {"record_id": "r3", "catalog_ids": []},
        }
        catalogs = [
            {"catalog_id": "c1", "record_ids": ["r1", "r2"]},
            {"catalog_id": "c2", "record_ids": ["r1"]},
        ]
        result = validate_record_catalog_inputs(records, catalogs)
        self.assertTrue(result["record_catalog_registry_valid"])
        self.assertEqual(result["record_catalogs_expected"], 3)
        self.assertEqual(result["record_catalogs_with_shared_references"], 2)
        self.assertEqual(result["record_catalogs_without_shared_references"], 1)
        self.assertEqual(result["record_catalog_shared_references"], 3)
        self.assertEqual(result["record_catalogs_with_provenance"], 2)

    def test_registry_validation_rejects_missing_catalog_reference(self):
        records = {"r1": {"record_id": "r1", "catalog_ids": ["missing"]}}
        with self.assertRaisesRegex(ValueError, "missing shared catalog reference"):
            validate_record_catalog_inputs(records, [])

    def test_registry_validation_rejects_catalog_missing_owner(self):
        records = {"r1": {"record_id": "r1", "catalog_ids": ["c1"]}}
        catalogs = [{"catalog_id": "c1", "record_ids": ["r2"]}]
        with self.assertRaisesRegex(ValueError, "does not contain owner"):
            validate_record_catalog_inputs(records, catalogs)

    def test_registry_validation_rejects_duplicate_catalog_references(self):
        records = {"r1": {"record_id": "r1", "catalog_ids": ["c1", "c1"]}}
        catalogs = [{"catalog_id": "c1", "record_ids": ["r1"]}]
        with self.assertRaisesRegex(ValueError, "duplicate shared catalog"):
            validate_record_catalog_inputs(records, catalogs)

    def test_registry_validation_rejects_duplicate_catalog_ids(self):
        records = {"r1": {"record_id": "r1", "catalog_ids": []}}
        catalogs = [
            {"catalog_id": "c1", "record_ids": ["r1"]},
            {"catalog_id": "c1", "record_ids": ["r1"]},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate shared catalog ID"):
            validate_record_catalog_inputs(records, catalogs)

    def test_registry_validation_rejects_record_map_key_mismatch(self):
        records = {"wrong-key": {"record_id": "r1", "catalog_ids": []}}
        with self.assertRaisesRegex(ValueError, "record map key mismatch"):
            validate_record_catalog_inputs(records, [])


if ( __name__ == "__main__" ):
    unittest.main()
