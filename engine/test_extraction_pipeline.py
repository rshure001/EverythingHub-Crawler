import unittest
from datetime import datetime, timezone
from extraction_pipeline import (
    normalize_extraction,
    attach_extractions,
    validate_extraction_authorization,
    MAX_TERMS_REVIEW_AGE_SECONDS,
)


class ExtractionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.asset = {
            "type": "video",
            "url": "https://media.example.org/mission.mp4",
            "title": "Apollo 11 film",
            "usage_rights": "Unknown",
            "rights_status": "unknown",
            "robots_allowed": True,
            "extraction_permission": True,
            "source_terms_status": "allow_extraction",
            "source_terms_url": "https://media.example.org/terms",
            "source_terms_checked_at": "2026-11-18T00:00:00Z",
            "source_terms_sha256": "a" * 64,
        }

    def record(self, **overrides):
        row = {
            "source_url": "https://media.example.org/mission.mp4",
            "extraction_type": "transcript",
            "text": "Apollo 11 mission control",
            "provider": "local-whisper",
            "model": "small.en",
            "confidence": 0.91,
        }
        row.update(overrides)
        return row

    def test_normalizes_transcript_with_provenance(self):
        result = normalize_extraction(self.record())
        self.assertEqual(result["field"], "transcript")
        self.assertEqual(result["provider"], "local-whisper")
        self.assertTrue(result["content_is_untrusted"])

    def test_builds_text_from_timestamped_segments(self):
        result = normalize_extraction(self.record(text="", segments=[
            {"start_seconds": 2, "end_seconds": 3, "text": "second"},
            {"start_seconds": 0, "end_seconds": 1, "text": "first"}]))
        self.assertEqual(result["text"], "first second")
        self.assertEqual(result["segments"][0]["start_seconds"], 0)

    def test_rejects_unsupported_type_missing_provenance_and_bad_url(self):
        for row in (
            self.record(extraction_type="executable"),
            self.record(provider=""),
            self.record(source_url="file:///tmp/a"),
            self.record(source_url="https://@media.example.org/a"),
        ):
            with self.assertRaises(ValueError):
                normalize_extraction(row)

    def test_skips_invalid_segments_and_clamps_confidence(self):
        result = normalize_extraction(self.record(text="", confidence=4, segments=[
            {"start_seconds": -1, "end_seconds": 2, "text": "bad"},
            {"start_seconds": 0, "end_seconds": 2, "text": "valid"}]))
        self.assertEqual(result["confidence"], 1)
        self.assertEqual(len(result["segments"]), 1)

    def test_bounds_segment_count_and_text(self):
        with self.assertRaises(ValueError):
            normalize_extraction(self.record(segments=[
                {"start_seconds": 0, "end_seconds": 1, "text": "x"}] * 501))

    def test_attaches_matching_text_but_not_other_asset_or_rights(self):
        rows = [
            self.record(),
            self.record(source_url="https://elsewhere.example/video.mp4", text="wrong"),
        ]
        result = attach_extractions(self.asset, rows)
        self.assertEqual(result["transcript"], "Apollo 11 mission control")
        self.assertEqual(result["usage_rights"], "Unknown")
        self.assertEqual(result["rights_status"], "unknown")
        self.assertEqual(len(result["extraction_provenance"]), 1)

    def test_scheme_and_host_case_are_normalized(self):
        row = self.record(source_url="HTTPS://MEDIA.EXAMPLE.ORG/mission.mp4")
        result = attach_extractions(self.asset, [row])
        self.assertEqual(result["transcript"], "Apollo 11 mission control")

    def test_path_and_query_case_are_preserved(self):
        wrong_path = self.record(source_url="https://media.example.org/Mission.mp4")
        wrong_query = self.record(source_url="https://media.example.org/mission.mp4?token=abc")
        asset_with_query = {**self.asset, "url": "https://media.example.org/mission.mp4?Token=ABC"}
        wrong_query_result = attach_extractions(asset_with_query, [wrong_query])
        self.assertEqual(wrong_query_result["transcript"], "")
        self.assertEqual(wrong_query_result["extraction_provenance"], [])
        wrong_path_result = attach_extractions(self.asset, [wrong_path])
        self.assertEqual(wrong_path_result["transcript"], "")
        self.assertEqual(wrong_path_result["extraction_provenance"], [])

    def test_matching_query_keeps_exact_case(self):
        asset = {**self.asset, "url": "https://media.example.org/mission.mp4?Token=ABC"}
        row = self.record(source_url="https://MEDIA.EXAMPLE.ORG/mission.mp4?Token=ABC")
        result = attach_extractions(asset, [row])
        self.assertEqual(result["transcript"], "Apollo 11 mission control")

    def test_rejects_empty_extraction(self):
        with self.assertRaises(ValueError):
            normalize_extraction(self.record(text=""))

    def test_requires_explicit_robots_and_terms_attestation(self):
        cases = (
            ({**self.asset, "robots_allowed": False}, "robots_allowed=true"),
            ({**self.asset, "robots_allowed": None}, "robots_allowed=true"),
            ({**self.asset, "extraction_permission": False}, "extraction_permission=true"),
            ({**self.asset, "source_terms_status": "unknown"}, "allow extraction"),
            ({**self.asset, "source_terms_url": "javascript:alert(1)"}, "source_terms_url"),
            ({**self.asset, "source_terms_sha256": "not-a-digest"}, "SHA-256"),
            ({k: v for k, v in self.asset.items() if k != "source_terms_checked_at"}, "checked_at"),
        )
        for asset, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    attach_extractions(asset, [self.record()])

    def test_rejects_stale_or_future_terms_reviews(self):
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        stale = {
            **self.asset,
            "source_terms_checked_at": datetime.fromtimestamp(
                now.timestamp() - MAX_TERMS_REVIEW_AGE_SECONDS - 1,
                tz=timezone.utc,
            ).isoformat(),
        }
        future = {**self.asset, "source_terms_checked_at": "2026-11-20T00:00:00Z"}
        for asset, message in ((stale, "older than 90 days"), (future, "future")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    validate_extraction_authorization(asset, now=now)

    def test_terms_review_must_have_timezone(self):
        asset = {**self.asset, "source_terms_checked_at": "2026-11-18T00:00:00"}
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            validate_extraction_authorization(
                asset, now=datetime(2026, 11, 18, tzinfo=timezone.utc)
            )

    def test_empty_extraction_does_not_require_permission(self):
        asset = {"type": "image", "url": "https://media.example.org/cover.jpg"}
        result = attach_extractions(asset, [])
        self.assertEqual(result["rights_status"], "unknown")
        self.assertEqual(result["extraction_provenance"], [])


if __name__ == "__main__":
    unittest.main()
