import unittest

from media_intelligence import (
    index_assets,
    is_safe_public_url,
    normalize_asset,
    normalize_discovery_result,
    search_assets,
)


class MultimodalIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.assets = [
            {
                "id": "img-1", "type": "image",
                "url": "https://media.example.org/launch.jpg",
                "title": "Apollo 11 launch photograph",
                "alt_text": "Saturn V rocket", "ocr_text": "NASA Apollo 11",
                "tags": ["space", "NASA", "space"], "width": 1920, "height": 1080,
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "rights_status": "verified", "attribution": "NASA",
            },
            {
                "id": "audio-1", "type": "audio",
                "url": "https://media.example.org/interview.mp3",
                "title": "Apollo mission interview",
                "transcript": "Saturn V carried Apollo 11 to the Moon",
                "duration_seconds": 93.4, "sample_rate": 48000, "channels": 2,
            },
            {
                "id": "video-1", "type": "video",
                "url": "https://media.example.org/mission.mp4",
                "title": "Apollo 11 documentary",
                "scene_descriptions": "Saturn V lifts off",
                "duration_seconds": 1200, "frame_rate": 29.97, "frame_count": 35964,
            },
            {
                "id": "design-1", "type": "design",
                "url": "https://media.example.org/poster.svg",
                "title": "Apollo mission poster",
                "design_text": "Mission typography gold lettering",
            },
        ]

    def test_all_four_media_types_normalize(self):
        self.assertEqual(
            [asset["media_type"] for asset in index_assets(self.assets)],
            ["image", "audio", "video", "design"],
        )

    def test_searches_ocr_transcript_scenes_and_design(self):
        self.assertEqual(search_assets("NASA Apollo", self.assets, media_type="image")[0]["asset_id"], "img-1")
        self.assertEqual(search_assets("Saturn V Moon", self.assets, media_type="audio")[0]["asset_id"], "audio-1")
        self.assertEqual(search_assets("Saturn V lifts off", self.assets, media_type="video")[0]["asset_id"], "video-1")
        self.assertEqual(search_assets("gold lettering", self.assets, media_type="design")[0]["asset_id"], "design-1")

    def test_shared_url_policy_rejects_unsafe_hosts_and_urls(self):
        self.assertTrue(is_safe_public_url("https://media.example.org/a.mp4"))
        for url in (
            "javascript:alert(1)",
            "https://@example.org/a",
            "https://example.org:bad/a",
            "http://127.0.0.1/a",
            "http://localhost/a",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_safe_public_url(url))

    def test_unknown_rights_never_upgraded(self):
        asset = normalize_asset({"type": "image", "url": "https://example.org/a.jpg"})
        self.assertEqual(asset["rights_status"], "unknown")
        self.assertIn("Unknown", asset["usage_rights"])

    def test_verified_requires_license_statement_and_safe_license_url(self):
        no_license = normalize_asset({
            "type": "image", "url": "https://example.org/a.jpg",
            "rights_status": "verified",
        })
        no_license_url = normalize_asset({
            "type": "image", "url": "https://example.org/b.jpg",
            "rights_status": "verified", "usage_rights": "CC BY 4.0",
            "license_url": "javascript:alert(1)",
        })
        self.assertEqual(no_license["rights_status"], "unknown")
        self.assertEqual(no_license_url["rights_status"], "unknown")
        self.assertEqual(normalize_asset(self.assets[0])["rights_status"], "verified")

    def test_invalid_types_and_unsafe_urls_rejected(self):
        invalid = (
            {"type": "document", "url": "https://example.org/a"},
            {"type": "image", "url": "javascript:alert(1)"},
            {"type": "image", "url": "https://user:pass@example.org/a"},
            {"type": "image", "url": "https://@example.org/a"},
            {"type": "image", "url": "https://example.org:bad/a"},
            {"type": "image", "url": "http://127.0.0.1/a"},
            {"type": "image", "url": "http://localhost/a"},
            None,
        )
        for row in invalid:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    normalize_asset(row)

    def test_robots_and_noindex_policies_are_fail_closed(self):
        for policy in (
            {"robots_allowed": False},
            {"robots_allowed": "false"},
            {"indexing_allowed": False},
            {"noindex": True},
            {"noindex": "noindex"},
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    normalize_asset({
                        "type": "image", "url": "https://example.org/a.jpg", **policy,
                    })
        self.assertEqual(len(index_assets([
            {**self.assets[0], "robots_allowed": False},
            self.assets[0],
        ])), 1)

    def test_deduplication_and_invalid_record_skip(self):
        rows = self.assets + [
            dict(self.assets[0], url="https://media.example.org/launch.jpg#copy"),
            {"type": "image", "url": "file:///tmp/a.jpg"},
        ]
        self.assertEqual(len(index_assets(rows)), 4)

    def test_nonfinite_values_tags_and_boolean_numbers_are_bounded(self):
        asset = normalize_asset({
            "type": "video", "url": "https://example.org/v.mp4",
            "duration_seconds": float("inf"), "frame_rate": -1,
            "sample_rate": True, "tags": ["x" * 500],
        })
        self.assertIsNone(asset["duration_seconds"])
        self.assertEqual(asset["frame_rate"], 0)
        self.assertIsNone(asset["sample_rate"])
        self.assertLessEqual(len(asset["tags"][0]), 80)

    def test_filter_is_validated_and_artwork_alias_is_supported(self):
        with self.assertRaises(ValueError):
            search_assets("apollo", self.assets, media_type="executable")
        self.assertEqual(
            search_assets("NASA Apollo", self.assets, media_type="artwork")[0]["media_type"],
            "image",
        )
        artwork = normalize_asset({
            "media_type": "artwork", "url": "https://example.org/art.jpg",
        })
        self.assertEqual(artwork["media_type"], "image")

    def test_discovery_result_maps_live_provider_fields_and_keeps_provenance(self):
        result = normalize_discovery_result({
            "title": "Apollo 11 photograph",
            "pageUrl": "https://commons.wikimedia.org/wiki/File:Apollo.jpg",
            "imageUrl": "https://upload.wikimedia.org/apollo.jpg",
            "thumbnailUrl": "https://upload.wikimedia.org/thumb.jpg",
            "mediaType": "image",
            "source": "Wikimedia Commons",
            "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "creator": "NASA",
            "credit": "NASA",
            "attributionRequired": True,
            "usageRights": "Review the linked license and attribution requirements before reuse",
        })
        self.assertEqual(result["url"], "https://commons.wikimedia.org/wiki/File:Apollo.jpg")
        self.assertEqual(result["source_page_url"], result["url"])
        self.assertEqual(result["asset_url"], "https://upload.wikimedia.org/apollo.jpg")
        self.assertEqual(result["thumbnail_url"], "https://upload.wikimedia.org/thumb.jpg")
        self.assertEqual(result["source"], "Wikimedia Commons")
        self.assertEqual(result["rights_status"], "unknown")
        self.assertEqual(result["license"], "CC BY 4.0")
        self.assertEqual(result["license_url"], "https://creativecommons.org/licenses/by/4.0/")
        self.assertEqual(result["credit"], "NASA")
        self.assertEqual(result["attribution"], "NASA")
        self.assertTrue(result["attribution_required"])

    def test_video_thumbnail_is_not_misrepresented_as_a_direct_video_asset(self):
        result = normalize_discovery_result({
            "title": "Apollo 11 documentary",
            "pageUrl": "https://www.example.org/watch/apollo",
            "videoUrl": "",
            "thumbnailUrl": "https://cdn.example.org/apollo-thumb.jpg",
            "mediaType": "video",
            "source": "Bing Videos",
            "usageRights": "Unverified — check the source page terms before reuse",
        })
        self.assertEqual(result["url"], "https://www.example.org/watch/apollo")
        self.assertEqual(result["asset_url"], "")
        self.assertEqual(result["thumbnail_url"], "https://cdn.example.org/apollo-thumb.jpg")
        self.assertEqual(result["rights_status"], "unverified")

    def test_search_deduplicates_same_media_result(self):
        duplicate = dict(self.assets[0], url="https://media.example.org/launch.jpg#copy")
        results = search_assets("NASA Apollo", self.assets + [duplicate], media_type="image")
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
