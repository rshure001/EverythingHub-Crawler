import unittest
from datetime import datetime, timezone

from intelligence_core import score_evidence, score_evidence_batch, rank_evidence, learn_weights

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


class IntelligenceCoreTests(unittest.TestCase):
    def setUp(self):
        self.pages = [
            {"url": "https://a.example.org/story", "title": "Story A",
             "topic_key": "topic-1", "relevance_score": 0.9,
             "source_quality": 0.8, "fetched_at": "2026-11-17T00:00:00Z"},
            {"url": "https://b.example.net/story", "title": "Story B",
             "topic_key": "topic-1", "relevance_score": 0.8,
             "source_quality": 0.7, "fetched_at": "2026-11-16T00:00:00Z"},
            {"url": "https://c.example.com/story", "title": "Story C",
             "topic_key": "topic-2", "relevance_score": 0.5,
             "source_quality": 0.5, "fetched_at": "2026-10-01T00:00:00Z"},
        ]

    def test_score_is_bounded_and_explainable(self):
        result = score_evidence(self.pages[0], self.pages, now=NOW)
        self.assertTrue(result["eligible"])
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 1)
        self.assertEqual(set(result["components"]), {
            "relevance", "source_quality", "freshness", "corroboration"})

    def test_independent_host_corroboration(self):
        result = score_evidence(self.pages[0], self.pages, now=NOW)
        self.assertEqual(result["corroborating_hosts"], 1)
        self.assertGreater(result["components"]["corroboration"], 0)

    def test_robots_denied_is_never_scored(self):
        denied = {**self.pages[0], "robots_allowed": False}
        result = score_evidence(denied, self.pages, now=NOW)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["score"], 0)

    def test_invalid_url_is_never_scored(self):
        for url in ("javascript:alert(1)", "https://", "ftp://example.org/file"):
            with self.subTest(url=url):
                result = score_evidence({"url": url}, now=NOW)
                self.assertFalse(result["eligible"])

    def test_rank_is_deterministic_and_deduplicated(self):
        ranked = rank_evidence(self.pages + [self.pages[0]], now=NOW)
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked, rank_evidence(self.pages, now=NOW))

    def test_batch_scoring_matches_individual_scores(self):
        batch = score_evidence_batch(self.pages, now=NOW)
        self.assertEqual(len(batch), len(self.pages))
        for page, result in zip(self.pages, batch):
            individual = score_evidence(page, self.pages, now=NOW)
            self.assertAlmostEqual(result["score"], individual["score"], places=6)
            self.assertEqual(result["corroborating_hosts"], individual["corroborating_hosts"])

    def test_learning_uses_only_explicit_valid_feedback(self):
        events = [
            {"label": "helpful", "feature_snapshot": {
                "relevance": 1, "source_quality": 1,
                "freshness": 0, "corroboration": 0}},
            {"label": "unknown", "feature_snapshot": {
                "relevance": 1, "source_quality": 1,
                "freshness": 0, "corroboration": 0}},
            {"label": "helpful", "feature_snapshot": {
                "relevance": 2, "source_quality": 1,
                "freshness": 0, "corroboration": 0}},
        ]
        result = learn_weights(events)
        self.assertEqual(result["feedback_used"], 1)
        self.assertEqual(result["feedback_ignored"], 2)
        self.assertGreater(result["weights"]["relevance"], 0.45)

    def test_learning_is_bounded_and_does_not_mutate_prior(self):
        prior = {"relevance": 0.45, "source_quality": 0.25,
                 "freshness": 0.15, "corroboration": 0.15}
        events = [{"label": "helpful", "feature_snapshot": {
            "relevance": 1, "source_quality": 1,
            "freshness": 0, "corroboration": 0}}] * 5000
        result = learn_weights(events, prior=prior)
        self.assertEqual(prior["relevance"], 0.45)
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0, places=5)
        self.assertTrue(all(0.0 < value < 1.0 for value in result["weights"].values()))

    def test_unknown_age_is_not_assumed_fresh(self):
        result = score_evidence({"url": "https://example.org",
                                 "relevance_score": 1}, now=NOW)
        self.assertEqual(result["components"]["freshness"], 0.35)


if __name__ == "__main__":
    unittest.main()
