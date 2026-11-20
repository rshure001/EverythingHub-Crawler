import unittest
from unittest.mock import patch

import catalog_openlibrary


class OpenLibraryCatalogTests(unittest.TestCase):
    def test_subject_response_becomes_book_record(self):
        payload = {
            "work_count": 1,
            "works": [{
                "key": "/works/OL123W",
                "title": "A Test Book",
                "first_publish_year": 2001,
                "edition_count": 3,
                "cover_id": 12345,
                "authors": [{"name": "A. Writer", "key": "/authors/OL9A"}],
                "subject": ["science", "history"],
            }],
        }
        with patch.object(catalog_openlibrary, "request_subject", return_value=payload):
            with patch.object(catalog_openlibrary, "SUBJECTS", ("science",)):
                result = catalog_openlibrary.discover(pause=0, allow_api=True)

        self.assertEqual(result["catalog_count"], 1)
        self.assertEqual(result["datasets_discovered"], 1)
        catalog = result["catalogs"][0]
        record = result["datasets"][0]
        self.assertEqual(catalog["catalog_type"], "library_archive")
        self.assertEqual(record["record_type"], "document")
        self.assertEqual(record["landing_url"], "https://openlibrary.org/works/OL123W")
        self.assertEqual(record["canonical_entity_key"], "openlibrary:work:OL123W")
        self.assertEqual(record["attributes"]["edition_count"], 3)

    def test_ignores_invalid_work_keys(self):
        payload = {"work_count": 1, "works": [{"key": "/authors/OL9A", "title": "Not a work"}]}
        with patch.object(catalog_openlibrary, "request_subject", return_value=payload):
            with patch.object(catalog_openlibrary, "SUBJECTS", ("science",)):
                result = catalog_openlibrary.discover(pause=0, allow_api=True)
        self.assertEqual(result["datasets_discovered"], 0)


    def test_default_api_discovery_is_disabled(self):
        with patch.object(catalog_openlibrary, "request_subject") as request:
            with self.assertRaises(catalog_openlibrary.SourcePolicyError):
                catalog_openlibrary.discover(pause=0)
            request.assert_not_called()

if __name__ == "__main__":
    unittest.main()
