import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "supabase" / "functions" / "everything-hub-media-search" / "index.ts"


class MediaDiscoveryComplianceTests(unittest.TestCase):
    def test_commons_results_request_license_and_attribution_metadata(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('iiprop: "url|mime|extmetadata"', source)
        self.assertIn("iiextmetadatafilter:", source)
        for field in ("LicenseShortName", "LicenseUrl", "Artist", "Credit", "AttributionRequired"):
            with self.subTest(field=field):
                self.assertIn(field, source)

    def test_bing_results_are_not_presented_as_reuse_licensed(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("Unverified", source)
        self.assertIn("verify reuse rights on the source page", source)

    def test_response_exposes_source_and_usage_rights(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('source: "Wikimedia Commons"', source)
        self.assertIn("usageRights:", source)
        self.assertIn("usageNote:", source)


if __name__ == "__main__":
    unittest.main()
