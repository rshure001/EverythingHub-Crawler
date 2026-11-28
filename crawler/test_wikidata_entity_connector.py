import unittest
from unittest.mock import patch

import wikidata_entity_connector as connector


class WikidataConnectorTests(unittest.TestCase):
    def test_normalizes_entity_with_provenance_and_cc0(self):
        payload = {
            "entities": {
                "Q42": {
                    "id": "Q42",
                    "labels": {"en": {"language": "en", "value": "Example camera"}},
                    "descriptions": {"en": {"language": "en", "value": "A test camera"}},
                    "aliases": {"en": [{"language": "en", "value": "Example photographic camera"}]},
                }
            }
        }
        record = connector.normalize_entity(payload, "Q42", "cameras")
        self.assertEqual(record["record_id"], "wikidata:Q42")
        self.assertEqual(record["catalog_id"], "cameras")
        self.assertEqual(record["title"], "Example camera")
        self.assertEqual(record["source"]["data_license"], "CC0-1.0")
        self.assertEqual(record["rights_status"], "structured_data_cc0")
        self.assertEqual(record["publication_status"], "staged_not_published")

    def test_rejects_response_for_wrong_entity(self):
        with self.assertRaises(ValueError):
            connector.normalize_entity({"entities": {"Q43": {"id": "Q43"}}}, "Q42", "cameras")

    def test_deduplicates_qids_and_keeps_errors_separate(self):
        payload = {
            "entities": {
                "Q42": {
                    "id": "Q42",
                    "labels": {"en": {"value": "Example camera"}},
                    "descriptions": {"en": {"value": "A test camera"}},
                    "aliases": {},
                }
            }
        }
        with patch.object(connector, "fetch_entity", return_value=payload) as fetch:
            records, errors = connector.import_entities(["Q42", "Q42"], "cameras", delay=0)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(errors, [])

    def test_rejects_invalid_qid(self):
        with self.assertRaises(ValueError):
            connector.fetch_entity("not-a-qid")


if __name__ == "__main__":
    unittest.main()
