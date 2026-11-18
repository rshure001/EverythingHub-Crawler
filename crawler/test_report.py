import unittest

from report import publication_date_coverage


class PublicationDateCoverageTests(unittest.TestCase):
    def test_counts_only_parseable_publication_dates(self):
        pages = [
            {"published_at": "2024-05-01T12:30:00Z"},
            {"published_at": "not-a-date"},
            {"published_at": None},
        ]
        count, percentage = publication_date_coverage(pages)
        self.assertEqual(count, 1)
        self.assertAlmostEqual(percentage, 100 / 3, places=4)

    def test_empty_index_has_zero_percent_coverage(self):
        self.assertEqual(publication_date_coverage([]), (0, 0.0))


if __name__ == "__main__":
    unittest.main()
