import gzip
import json
import tempfile
import unittest
from pathlib import Path

from catalog_page_analysis import (
    analyze_pages,
    analyze_pages_incremental,
    stable_id,
    validate_outputs,
    write_outputs,
)


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
        self.assertEqual(result["metrics"]["eligible_source_pages_with_catalogs"], 0)
        self.assertEqual(result["metrics"]["eligible_source_pages_without_catalogs"], 1)
        self.assertEqual(result["metrics"]["eligible_source_pages_with_multiple_catalogs"], 0)
        self.assertEqual(len(result["page_catalogs"]), 1)
        self.assertEqual(result["record_catalogs"], [])
        self.assertEqual(result["metrics"]["page_owned_identity_catalogs"], 1)
        self.assertEqual(result["metrics"]["record_owned_identity_catalogs"], 0)
        self.assertEqual(result["metrics"]["identity_catalogs_total"], 1)
        page_identity = result["page_catalogs"][0]
        self.assertEqual(
            page_identity["catalog_id"],
            stable_id("catalog", "__page__|" + result["pages"][0]["page_id"]),
        )
        self.assertEqual(page_identity["record_ids"], [])
        self.assertFalse(page_identity["public_search_allowed"])
        self.assertFalse(page_identity["verified"])
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
        self.assertEqual(result["metrics"]["eligible_source_pages_with_catalogs"], 2)
        self.assertEqual(result["metrics"]["eligible_source_pages_without_catalogs"], 0)
        self.assertEqual(result["metrics"]["eligible_source_pages_with_multiple_catalogs"], 0)
        real_catalog_ids = {catalog["catalog_id"] for catalog in result["catalogs"]}
        self.assertEqual(len(result["page_catalogs"]), 2)
        self.assertEqual(len(result["record_catalogs"]), len(records))
        page_identities = {item["page_id"]: item for item in result["page_catalogs"]}
        record_identities = {item["record_id"]: item for item in result["record_catalogs"]}
        self.assertTrue(set(page_identities).issubset({page["page_id"] for page in result["pages"]}))
        for record in records.values():
            self.assertEqual(
                record["record_id"],
                stable_id("record", record["canonical_entity_key"]),
            )
            self.assertNotIn("record_catalog_id", record)
            self.assertNotIn("page_catalog_ids", record)
            self.assertTrue(record["source_page_ids"])
            self.assertTrue(set(record.get("catalog_ids", [])).issubset(real_catalog_ids))
            identity = record_identities[record["record_id"]]
            self.assertEqual(identity["catalog_id"], stable_id("record-catalog", record["record_id"]))
            self.assertEqual(identity["record_ids"], [record["record_id"]])
            self.assertEqual(
                identity["page_catalog_ids"],
                sorted(stable_id("catalog", "__page__|" + page_id) for page_id in record["source_page_ids"]),
            )
            self.assertFalse(identity["public_search_allowed"])
            self.assertFalse(identity["verified"])
        for page in result["pages"]:
            identity = page_identities[page["page_id"]]
            self.assertEqual(identity["catalog_id"], stable_id("catalog", "__page__|" + page["page_id"]))
            self.assertEqual(identity["record_ids"], page["record_ids"])
            self.assertFalse(identity["public_search_allowed"])
            self.assertNotIn("page_catalog_id", page)
            self.assertNotIn("record_catalog_ids", page)
            for record_id in page["record_ids"]:
                self.assertIn(page["page_id"], records[record_id]["source_page_ids"])


    def test_incremental_identity_sidecars_stream_and_validate_separately(self):
        pages = [
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
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            analysis = analyze_pages_incremental(
                pages,
                source_total=len(pages),
                cache_dir=root / "cache",
            )
            manifest = write_outputs(analysis, root / "output")
            validated = validate_outputs(root / "output")
            with gzip.open(root / "output" / "page-catalogs.jsonl.gz", "rt", encoding="utf-8") as stream:
                page_header = json.loads(stream.readline())
                page_rows = [json.loads(line) for line in stream if line.strip()]
            record_shard_paths = sorted((root / "output" / "record-catalogs").glob("*.jsonl.gz"))
            record_headers = []
            record_rows = []
            for shard_path in record_shard_paths:
                with gzip.open(shard_path, "rt", encoding="utf-8") as stream:
                    record_headers.append(json.loads(stream.readline()))
                    record_rows.extend(json.loads(line) for line in stream if line.strip())
            self.assertTrue(record_headers)
            record_header = record_headers[0]
            self.assertTrue(all(header["kind"] == "record_identity_catalogs" for header in record_headers))
            self.assertTrue(all(header["items"] == len(analysis["records"]) for header in record_headers))
            registry = json.loads(gzip.decompress((root / "output" / "registry.json.gz").read_bytes()))
        self.assertEqual(page_header["kind"], "page_identity_catalogs")
        self.assertEqual(record_header["kind"], "record_identity_catalogs")
        self.assertEqual(page_header["items"], len(pages))
        self.assertEqual(record_header["items"], len(analysis["records"]))
        self.assertEqual(len(page_rows), len(pages))
        self.assertEqual(len(record_rows), len(analysis["records"]))
        self.assertEqual(manifest["page_identity_catalog_count"], len(pages))
        self.assertEqual(manifest["record_identity_catalog_count"], len(analysis["records"]))
        self.assertEqual(validated["page_identity_catalogs_validated"], len(pages))
        self.assertEqual(validated["record_identity_catalogs_validated"], len(analysis["records"]))
        registry_ids = {item["catalog_id"] for item in registry["catalogs"]}
        self.assertTrue(all(item["catalog_id"] not in registry_ids for item in page_rows))
        self.assertTrue(all(item["catalog_id"] not in registry_ids for item in record_rows))



if __name__ == "__main__":
    unittest.main()
