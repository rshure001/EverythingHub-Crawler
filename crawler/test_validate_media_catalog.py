import unittest

from validate_media_catalog import validate_document


def candidate(**changes):
    row = {
        "media_type": "image",
        "url": "https://media.example.org/image.jpg",
        "source_page_url": "https://media.example.org/story",
        "source_pages": ["https://media.example.org/story"],
        "title": "Example image",
        "alt_text": "A test image",
        "thumbnail_url": "",
        "robots_allowed": True,
        "policy_status": "robots_allowed_not_fetched",
        "rights_status": "unknown",
        "usage_rights": "Unknown",
        "first_seen_at": 100,
        "last_seen_at": 100,
        "seen_count": 1,
    }
    row.update(changes)
    return row


def document(*rows):
    return {
        "version": 1,
        "updated_at": 100,
        "total_candidates": len(rows),
        "candidates": list(rows),
    }


class MediaCatalogValidationTests(unittest.TestCase):
    def test_valid_metadata_only_candidate(self):
        errors, metrics = validate_document(document(candidate()))
        self.assertEqual(errors, [])
        self.assertEqual(metrics["candidates"], 1)
        self.assertEqual(metrics["robots_allowed"], 1)
        self.assertEqual(metrics["robots_timestamped"], 0)
        self.assertEqual(metrics["metadata_only_not_fetched"], 1)
        self.assertEqual(metrics["unknown_rights"], 1)

    def test_valid_timestamped_robots_decision(self):
        errors, metrics = validate_document(document(candidate(robots_checked_at=100)))
        self.assertEqual(errors, [])
        self.assertEqual(metrics["robots_timestamped"], 1)

    def test_rejects_invalid_or_future_relative_robots_timestamp(self):
        for value in (0, -1, True, "100", 101):
            with self.subTest(value=value):
                errors, _ = validate_document(document(candidate(robots_checked_at=value)))
                self.assertTrue(any("robots_checked_at" in error for error in errors))

    def test_valid_image_audio_and_video_types(self):
        rows = [
            candidate(media_type="image"),
            candidate(media_type="audio", url="https://media.example.org/audio.mp3"),
            candidate(media_type="video", url="https://media.example.org/video.mp4"),
        ]
        errors, metrics = validate_document(document(*rows))
        self.assertEqual(errors, [])
        self.assertEqual(metrics["by_type"], {"audio": 1, "image": 1, "video": 1})

    def test_rejects_catalog_count_mismatch(self):
        data = document(candidate())
        data["total_candidates"] = 2
        errors, _ = validate_document(data)
        self.assertTrue(any("total_candidates" in error for error in errors))

    def test_rejects_duplicate_media_records(self):
        errors, _ = validate_document(document(candidate(), candidate()))
        self.assertTrue(any("duplicates" in error for error in errors))

    def test_rejects_cross_origin_media(self):
        row = candidate(url="https://cdn.example.net/image.jpg")
        errors, _ = validate_document(document(row))
        self.assertTrue(any("same-origin" in error for error in errors))

    def test_rejects_cross_origin_or_unsafe_source_pages(self):
        row = candidate(source_pages=[
            "https://media.example.org/story",
            "https://other.example.net/story",
        ])
        errors, _ = validate_document(document(row))
        self.assertTrue(any("cross-origin source page" in error for error in errors))

    def test_rejects_signed_and_credential_bearing_urls(self):
        for url in (
            "https://media.example.org/image.jpg?X-Amz-Signature=secret",
            "https://user:pass@media.example.org/image.jpg",
            "https://media.example.org/image.jpg?access_token=secret",
        ):
            with self.subTest(url=url):
                errors, _ = validate_document(document(candidate(url=url)))
                self.assertTrue(errors)

    def test_rejects_robots_denied_or_unfetched_policy_mismatch(self):
        for changes in (
            {"robots_allowed": False},
            {"policy_status": "fetched"},
        ):
            with self.subTest(changes=changes):
                errors, _ = validate_document(document(candidate(**changes)))
                self.assertTrue(any("robots" in error or "metadata-only" in error for error in errors))

    def test_rejects_unverified_reuse_claims(self):
        errors, _ = validate_document(document(candidate(
            rights_status="licensed",
            usage_rights="Free to reuse",
        )))
        self.assertTrue(any("reuse rights" in error for error in errors))

    def test_rejects_malformed_media_type_and_source_page_entries(self):
        errors, _ = validate_document(document(candidate(media_type=[])))
        self.assertTrue(any("unsupported media_type" in error for error in errors))
        errors, _ = validate_document(document(candidate(source_pages=[{"url": "bad"}])))
        self.assertTrue(any("entries must be strings" in error for error in errors))

    def test_rejects_invalid_timestamps_and_seen_count(self):
        errors, _ = validate_document(document(candidate(
            first_seen_at=200,
            last_seen_at=100,
            seen_count=0,
        )))
        self.assertTrue(any("precedes" in error for error in errors))
        self.assertTrue(any("seen_count" in error for error in errors))

    def test_rejects_tracking_and_fragment_urls_as_noncanonical(self):
        row = candidate(url="https://media.example.org/image.jpg?utm_source=feed#preview")
        errors, _ = validate_document(document(row))
        self.assertTrue(any("non-canonical" in error for error in errors))

    def test_bounds_source_page_provenance(self):
        row = candidate(source_pages=[
            f"https://media.example.org/story/{index}" for index in range(6)
        ])
        errors, _ = validate_document(document(row))
        self.assertTrue(any("source_pages must contain" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
