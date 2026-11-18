import unittest

from media_discovery import discover_media, merge_media_candidates, safe_media_url


class MediaDiscoveryTests(unittest.TestCase):
    def test_extracts_open_graph_html_and_container_media(self):
        html = """
        <html><head>
          <meta property="og:image" content="/images/mission.jpg">
          <meta property="og:video" content="/video/launch.mp4">
          <meta property="og:audio" content="/audio/countdown.mp3">
        </head><body>
          <img src="/img/earth.webp" alt="Earth" title="Earth view">
          <picture><source type="image/avif" srcset="/img/earth.avif 1x, /img/earth@2x.avif 2x"></picture>
          <video poster="/thumb/launch.jpg"><source src="/video/launch.webm" type="video/webm"></video>
          <audio><source src="/audio/voice.ogg" type="audio/ogg"></audio>
        </body></html>
        """
        rows = discover_media(html, "https://media.example.org/story", "Mission story")
        kinds = {row["media_type"] for row in rows}
        urls = {row["url"] for row in rows}
        self.assertEqual(kinds, {"image", "video", "audio"})
        self.assertIn("https://media.example.org/images/mission.jpg", urls)
        self.assertIn("https://media.example.org/video/launch.webm", urls)
        self.assertIn("https://media.example.org/audio/voice.ogg", urls)
        video = next(row for row in rows if row["url"].endswith("/video/launch.webm"))
        self.assertEqual(video["thumbnail_url"], "https://media.example.org/thumb/launch.jpg")

    def test_deduplicates_candidates_and_bounds_per_page(self):
        html = '<meta property="og:image" content="/one.jpg">' + ''.join(
            f'<img src="/{i}.jpg">' for i in range(30)
        ) + '<img src="/one.jpg" alt="First image">'
        rows = discover_media(html, "https://media.example.org/page")
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(row["url"].endswith("/one.jpg") for row in rows), 1)

    def test_rejects_private_credentials_and_signed_urls(self):
        for url in (
            "http://127.0.0.1/private.png",
            "http://localhost/private.png",
            "https://user:pass@media.example.org/image.jpg",
            "https://media.example.org/image.jpg?X-Amz-Signature=secret",
            "https://media.example.org/image.jpg?access_token=secret",
            "javascript:alert(1)",
            "data:image/png;base64,abc",
        ):
            self.assertEqual(safe_media_url("https://media.example.org/page", url), "")

    def test_strips_tracking_and_fragments_but_preserves_path_case(self):
        url = safe_media_url(
            "https://media.example.org/page",
            "/Images/Launch.JPG?utm_source=feed&size=large#preview",
        )
        self.assertEqual(url, "https://media.example.org/Images/Launch.JPG?size=large")

    def test_does_not_discover_from_unsafe_source_page(self):
        self.assertEqual(
            discover_media('<img src="/image.jpg">',
                           "https://media.example.org/story?token=secret"),
            [],
        )

    def test_merge_only_persists_robots_allowed_unknown_rights_candidates(self):
        candidate = {
            "media_type": "image",
            "url": "https://media.example.org/image.jpg",
            "source_page_url": "https://media.example.org/story",
            "title": "Image",
            "alt_text": "A picture",
            "robots_allowed": True,
            "robots_checked_at": 100,
        }
        denied = {**candidate, "url": "https://media.example.org/denied.jpg",
                  "robots_allowed": False}
        merged, added = merge_media_candidates([], [candidate, denied], now=100)
        self.assertEqual(added, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["rights_status"], "unknown")
        self.assertEqual(merged[0]["usage_rights"], "Unknown")
        self.assertEqual(merged[0]["policy_status"], "robots_allowed_not_fetched")
        self.assertEqual(merged[0]["robots_checked_at"], 100)

    def test_merge_deduplicates_and_refreshes_robots_decision_timestamp(self):
        first = {
            "media_type": "image", "url": "https://media.example.org/image.jpg",
            "source_page_url": "https://media.example.org/one",
            "robots_allowed": True, "robots_checked_at": 100,
        }
        existing, added = merge_media_candidates([], [first], now=100)
        second = {**first, "source_page_url": "https://media.example.org/two",
                  "robots_checked_at": 200}
        merged, new_count = merge_media_candidates(existing, [second], now=200)
        self.assertEqual(added, 1)
        self.assertEqual(new_count, 0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["seen_count"], 2)
        self.assertEqual(merged[0]["source_pages"], [
            "https://media.example.org/one", "https://media.example.org/two"
        ])
        self.assertEqual(merged[0]["last_seen_at"], 200)
        self.assertEqual(merged[0]["robots_checked_at"], 200)

    def test_legacy_candidate_gets_fresh_timestamp_when_rediscovered(self):
        legacy = {
            "media_type": "image", "url": "https://media.example.org/image.jpg",
            "source_page_url": "https://media.example.org/one",
            "robots_allowed": True, "last_seen_at": 50,
            "source_pages": ["https://media.example.org/one"],
        }
        incoming = {**legacy, "source_page_url": "https://media.example.org/two",
                    "robots_checked_at": 300}
        merged, added = merge_media_candidates([legacy], [incoming], now=300)
        self.assertEqual(added, 0)
        self.assertEqual(merged[0]["robots_checked_at"], 300)
        self.assertEqual(merged[0]["last_seen_at"], 300)

    def test_merge_rejects_missing_or_future_robots_check_timestamps(self):
        candidate = {
            "media_type": "image",
            "url": "https://media.example.org/image.jpg",
            "source_page_url": "https://media.example.org/story",
            "robots_allowed": True,
        }
        missing, missing_count = merge_media_candidates([], [candidate], now=100)
        future, future_count = merge_media_candidates(
            [], [{**candidate, "robots_checked_at": 101}], now=100
        )
        self.assertEqual(missing, [])
        self.assertEqual(missing_count, 0)
        self.assertEqual(future, [])
        self.assertEqual(future_count, 0)


if __name__ == "__main__":
    unittest.main()
