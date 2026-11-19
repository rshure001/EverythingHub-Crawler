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

    def test_commons_requests_respect_maxlag_and_identify_the_client(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('maxlag: "5"', source)
        self.assertIn('"User-Agent": "EverythingHub/1.0 (https://github.com/rshure001/EverythingHub-Crawler; media discovery)"', source)

    def test_response_exposes_source_and_usage_rights(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('source: "Wikimedia Commons"', source)
        self.assertIn("usageRights:", source)
        self.assertIn("usageNote:", source)


    def test_edge_responses_use_short_shared_cache_and_bounded_provider_timeouts(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('"Cache-Control": "public, max-age=180, s-maxage=180"', source)
        self.assertEqual(source.count("AbortSignal.timeout(9000)"), 2)
        self.assertIn('gsrlimit: "24"', source)
        self.assertIn('.slice(0, 30)', source)

    def test_live_media_smoke_covers_images_videos_and_reuse_rights(self):
        workflow = (SOURCE.parents[3] / ".github" / "workflows" / "media-discovery-smoke.yml").read_text(encoding="utf-8")
        self.assertIn('q=latest technology science nature', workflow)
        self.assertIn('q=latest science technology discoveries', workflow)
        self.assertIn('cache-control: public, max-age=180, s-maxage=180', workflow)
        self.assertIn('Unverified', workflow)


    def test_homepage_media_queries_match_live_smoke_queries(self):
        homepage = (SOURCE.parents[3] / "vercel-homepage" / "index.html").read_text(encoding="utf-8")
        self.assertIn("loadHomepageMedia('image','latest technology science nature','homeLiveImages')", homepage)
        self.assertIn("loadHomepageMedia('video','latest science technology discoveries','homeLiveVideos')", homepage)


    def test_bing_and_commons_are_queried_independently_and_merged(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('import { mergeMediaResults, mediaProviderLabel } from "./merge-results.mjs";', source)
        self.assertIn("Promise.allSettled([", source)
        self.assertIn("bing(query, type)", source)
        self.assertIn("commons(query, type)", source)
        self.assertIn("mergeMediaResults(bingResults, commonsResults)", source)
        self.assertIn("mediaProviderLabel(results, type)", source)


if __name__ == "__main__":
    unittest.main()
