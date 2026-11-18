import unittest
from datetime import datetime, timedelta, timezone

from media_extraction_preflight import evaluate_preflight

NOW = datetime(2026, 11, 18, 21, 55, tzinfo=timezone.utc)


def candidate(**changes):
    row = {
        "media_type": "image",
        "url": "https://media.example.org/image.jpg",
        "source_page_url": "https://media.example.org/story",
        "robots_allowed": True,
        "robots_checked_at": int(NOW.timestamp()),
        "last_seen_at": int(NOW.timestamp()),
        "policy_status": "robots_allowed_not_fetched",
        "rights_status": "unknown",
        "usage_rights": "Unknown",
    }
    row.update(changes)
    return row


def catalog(*rows):
    return {"version": 1, "candidates": list(rows)}


def review(**changes):
    row = {
        "origin": "https://media.example.org",
        "extraction_permission": True,
        "source_terms_status": "allow_extraction",
        "source_terms_url": "https://media.example.org/terms",
        "source_terms_sha256": "a" * 64,
        "source_terms_checked_at": NOW.isoformat(),
    }
    row.update(changes)
    return row


def registry(*rows):
    return {"version": 1, "reviews": list(rows)}


class MediaExtractionPreflightTests(unittest.TestCase):
    def test_missing_terms_approval_fails_closed(self):
        result = evaluate_preflight(catalog(candidate()), registry(), now=NOW)
        self.assertEqual(result["eligible_for_extraction_preflight"], 0)
        self.assertEqual(result["deferred_by_reason"]["missing_source_terms_approval"], 1)

    def test_valid_approval_only_makes_candidate_preflight_eligible(self):
        result = evaluate_preflight(catalog(candidate()), registry(review()), now=NOW)
        self.assertEqual(result["eligible_for_extraction_preflight"], 1)
        self.assertEqual(result["eligible_by_type"], {"image": 1})
        self.assertEqual(result["media_downloads"], 0)
        self.assertEqual(result["extractions_performed"], 0)
        self.assertEqual(result["redistribution_authorizations_granted"], 0)

    def test_robots_denial_and_missing_timestamp_are_rejected(self):
        rows = [
            candidate(robots_allowed=False),
            candidate(robots_checked_at=None),
        ]
        result = evaluate_preflight(catalog(*rows), registry(review()), now=NOW)
        self.assertEqual(result["deferred_by_reason"]["robots_not_allowed"], 1)
        self.assertEqual(result["deferred_by_reason"]["invalid_robots_check_timestamp"], 1)

    def test_stale_and_future_robots_decisions_are_rejected(self):
        old = int((NOW - timedelta(hours=25)).timestamp())
        future = int((NOW + timedelta(minutes=5)).timestamp())
        result = evaluate_preflight(
            catalog(candidate(robots_checked_at=old), candidate(robots_checked_at=future)),
            registry(review()), now=NOW,
        )
        self.assertEqual(result["deferred_by_reason"]["stale_robots_check"], 1)
        self.assertEqual(result["deferred_by_reason"]["future_robots_check"], 1)

    def test_robot_check_cannot_postdate_candidate_last_seen(self):
        result = evaluate_preflight(
            catalog(candidate(last_seen_at=int(NOW.timestamp()) - 1)),
            registry(review()), now=NOW,
        )
        self.assertEqual(result["deferred_by_reason"]["invalid_robots_check_timestamp"], 1)

    def test_metadata_must_remain_unfetched_and_same_origin(self):
        result = evaluate_preflight(
            catalog(
                candidate(policy_status="fetched"),
                candidate(url="https://cdn.example.net/image.jpg"),
            ),
            registry(review()), now=NOW,
        )
        self.assertEqual(result["deferred_by_reason"]["not_metadata_only"], 1)
        self.assertEqual(result["deferred_by_reason"]["unsafe_or_cross_origin_url"], 1)

    def test_stale_future_and_naive_terms_reviews_are_rejected(self):
        stale = review(source_terms_checked_at=(NOW - timedelta(days=91)).isoformat())
        future = review(source_terms_checked_at=(NOW + timedelta(days=1)).isoformat())
        naive = review(source_terms_checked_at="2026-11-18T21:55:00")
        result = evaluate_preflight(catalog(candidate()), registry(stale, future, naive), now=NOW)
        self.assertEqual(result["eligible_for_extraction_preflight"], 0)
        self.assertEqual(result["registry_reviews"]["invalid_reviews"], 3)

    def test_denied_and_duplicate_origin_reviews_fail_closed(self):
        denied = review(extraction_permission=False, source_terms_status="deny_extraction")
        denied_result = evaluate_preflight(catalog(candidate()), registry(denied), now=NOW)
        self.assertEqual(denied_result["deferred_by_reason"]["source_terms_denied"], 1)

        duplicate_result = evaluate_preflight(
            catalog(candidate()), registry(review(), review()), now=NOW
        )
        self.assertEqual(duplicate_result["deferred_by_reason"]["ambiguous_source_terms_reviews"], 1)
        self.assertEqual(duplicate_result["registry_reviews"]["duplicate_origins"], 1)

    def test_registry_origin_must_be_a_bare_origin(self):
        for origin in ("https://media.example.org/path", "https://media.example.org?x=1"):
            with self.subTest(origin=origin):
                result = evaluate_preflight(
                    catalog(candidate()), registry(review(origin=origin)), now=NOW
                )
                self.assertEqual(result["eligible_for_extraction_preflight"], 0)
                self.assertEqual(result["registry_reviews"]["invalid_reviews"], 1)

    def test_unsafe_terms_url_and_bad_digest_fail_closed(self):
        bad = review(
            source_terms_url="https://user:pass@media.example.org/terms",
            source_terms_sha256="not-a-digest",
        )
        result = evaluate_preflight(catalog(candidate()), registry(bad), now=NOW)
        self.assertEqual(result["deferred_by_reason"]["invalid_or_stale_source_terms_review"], 1)
        self.assertEqual(result["registry_reviews"]["invalid_reviews"], 1)

    def test_unknown_reuse_rights_do_not_become_redistribution_permission(self):
        result = evaluate_preflight(catalog(candidate()), registry(review()), now=NOW)
        self.assertEqual(result["rights_unknown_candidates"], 1)
        self.assertEqual(result["redistribution_authorizations_granted"], 0)

    def test_missing_registry_is_reported_and_never_approves(self):
        result = evaluate_preflight(
            catalog(candidate()), registry(), now=NOW, registry_missing=True
        )
        self.assertTrue(result["registry_missing"])
        self.assertEqual(result["eligible_for_extraction_preflight"], 0)


if __name__ == "__main__":
    unittest.main()
