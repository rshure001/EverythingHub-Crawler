import unittest

from media_intelligence import (
    MEDIA_TYPES,
    media_inventory,
    normalize_media_record,
    rank_media,
    tokenize,
)


class MediaIntelligenceTests(unittest.TestCase):
    def test_supported_modalities_are_explicit(self):
        self.assertEqual(MEDIA_TYPES, ("artwork", "audio", "video", "design"))

    def test_image_provider_shape_normalizes_to_artwork_and_keeps_rights(self):
        record = {
            "title": "Apollo 11 mission photograph",
            "pageUrl": "https://commons.wikimedia.org/wiki/File:Apollo.jpg",
            "imageUrl": "https://upload.wikimedia.org/apollo.jpg",
            "source": "Wikimedia Commons",
            "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "creator": "NASA",
            "attributionRequired": True,
            "usageRights": "Review the linked license and attribution requirements before reuse",
            "robots_allowed": True,
        }
        result = normalize_media_record(record)
        self.assertEqual(result["media_type"], "artwork")
        self.assertEqual(result["rights_status"], "review_required")
        self.assertTrue(result["attribution_required"])
        self.assertEqual(result["license"], "CC BY 4.0")
        self.assertEqual(result["creator"], "NASA")
        self.assertEqual(result["policy_status"], "robots_allowed")

    def test_audio_video_and_design_are_supported_without_provider_claims(self):
        cases = [
            ({"type": "audio", "pageUrl": "https://example.org/audio", "audioUrl": "https://cdn.example.org/clip.mp3"}, "audio"),
            ({"type": "video", "pageUrl": "https://example.org/video", "videoUrl": "https://cdn.example.org/clip.mp4", "thumbnailUrl": "https://cdn.example.org/thumb.jpg"}, "video"),
            ({"asset_kind": "mockup", "pageUrl": "https://example.org/design", "designUrl": "https://cdn.example.org/layout.fig", "previewUrl": "https://cdn.example.org/preview.png"}, "design"),
        ]
        for record, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(normalize_media_record(record)["media_type"], expected)

    def test_mime_and_extension_inference(self):
        self.assertEqual(
            normalize_media_record({
                "pageUrl": "https://example.org/a",
                "mediaUrl": "https://cdn.example.org/file.ogg",
                "mimeType": "audio/ogg",
            })["media_type"],
            "audio",
        )
        self.assertEqual(
            normalize_media_record({
                "pageUrl": "https://example.org/d",
                "mediaUrl": "https://cdn.example.org/file.woff2",
            })["media_type"],
            "design",
        )

    def test_unsafe_urls_and_robots_denied_records_are_rejected(self):
        base = {"type": "audio", "pageUrl": "https://example.org/audio", "audioUrl": "https://cdn.example.org/a.mp3"}
        self.assertIsNone(normalize_media_record({**base, "robots_allowed": False}))
        self.assertIsNone(normalize_media_record({**base, "pageUrl": "javascript:alert(1)"}))
        self.assertIsNone(normalize_media_record({**base, "audioUrl": "https://user:pass@cdn.example.org/a.mp3"}))
        self.assertIsNone(normalize_media_record({**base, "audioUrl": "file:///tmp/a.mp3"}))
        self.assertIsNone(normalize_media_record({**base, "pageUrl": "https://example.org:bad/audio"}))

    def test_audio_requires_a_direct_audio_asset(self):
        self.assertIsNone(normalize_media_record({
            "type": "audio",
            "pageUrl": "https://example.org/audio",
            "thumbnailUrl": "https://cdn.example.org/waveform.png",
        }))

    def test_license_metadata_never_becomes_automatic_reuse_approval(self):
        record = {
            "type": "artwork",
            "pageUrl": "https://example.org/art",
            "imageUrl": "https://cdn.example.org/art.jpg",
            "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "usageRights": "Unverified — check the source page terms before reuse",
        }
        result = normalize_media_record(record)
        self.assertEqual(result["rights_status"], "unverified")
        self.assertIsNone(normalize_media_record({**record, "licenseUrl": "javascript:alert(1)"})["license_url"])

    def test_search_ranks_title_then_filters_modality_and_deduplicates(self):
        records = [
            {"type": "artwork", "title": "Hurricane Core artwork", "pageUrl": "https://a.example/art", "imageUrl": "https://cdn.example/a.jpg"},
            {"type": "artwork", "title": "Core weather photograph", "pageUrl": "https://b.example/photo", "imageUrl": "https://cdn.example/b.jpg", "description": "Hurricane Core"},
            {"type": "video", "title": "Hurricane Core explainer", "pageUrl": "https://c.example/video", "videoUrl": "https://cdn.example/c.mp4"},
        ]
        results = rank_media(records + [records[0]], "Hurricane Core")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["title"], "Hurricane Core artwork")
        self.assertEqual(len(rank_media(records, "Hurricane", media_type="image")), 2)
        self.assertEqual(len(rank_media(records, "Hurricane", media_type="video")), 1)
        self.assertEqual(rank_media(records, "   "), [])

    def test_query_and_result_limits_are_bounded(self):
        self.assertEqual(tokenize("ＲＯＬＥＸ — Café"), ["rolex", "café"])
        records = [
            {"type": "artwork", "title": f"Hurricane {index}", "pageUrl": f"https://p{index}.example/x", "imageUrl": f"https://cdn.example/{index}.jpg"}
            for index in range(60)
        ]
        self.assertLessEqual(len(rank_media(records, "Hurricane", limit=1000)), 50)
        self.assertEqual(rank_media(records, "x" * 500), [])

    def test_inventory_reports_all_modalities_and_rights(self):
        records = [
            {"type": "artwork", "pageUrl": "https://a.example/a", "imageUrl": "https://cdn.example/a.jpg"},
            {"type": "audio", "pageUrl": "https://b.example/a", "audioUrl": "https://cdn.example/a.mp3"},
            {"type": "video", "pageUrl": "https://c.example/a", "videoUrl": "https://cdn.example/a.mp4"},
            {"asset_kind": "template", "pageUrl": "https://d.example/a", "designUrl": "https://cdn.example/a.fig"},
            {"type": "audio", "pageUrl": "javascript:alert(1)", "audioUrl": "https://cdn.example/b.mp3"},
        ]
        result = media_inventory(records)
        self.assertEqual(result["accepted"], 4)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["by_modality"], {"artwork": 1, "audio": 1, "video": 1, "design": 1})
        self.assertEqual(result["by_rights_status"]["unverified"], 4)


if __name__ == "__main__":
    unittest.main()
