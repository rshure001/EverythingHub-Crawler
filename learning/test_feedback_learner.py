import json
import tempfile
import unittest
from pathlib import Path

from feedback_learner import build_candidate, load_feedback


class FeedbackLearnerTests(unittest.TestCase):
    def test_requires_minimum_samples_and_multiple_lanes(self):
        result = build_candidate({"web": [1] * 19, "news": [0] * 30})
        self.assertEqual(result["status"], "insufficient_feedback")
        self.assertEqual(result["lane_weights"], {})

    def test_builds_bounded_review_candidate(self):
        result = build_candidate({
            "web": [1] * 40 + [0] * 2,
            "news": [1] * 5 + [0] * 37,
        })
        self.assertEqual(result["status"], "candidate_requires_review")
        self.assertFalse(result["production_applied"])
        self.assertTrue(all(0.90 <= value <= 1.10 for value in result["lane_weights"].values()))
        self.assertGreater(result["lane_weights"]["web"], result["lane_weights"]["news"])

    def test_rejects_bad_or_boolean_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            path.write_text(json.dumps({"lane": "web", "label": True}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_feedback(path)

    def test_rejects_identifying_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            path.write_text('{"lane":"web","label":1,"user_id":"private"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                load_feedback(path)

    def test_reads_minimal_anonymous_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            path.write_text('{"lane":"Web","label":1}\n{"lane":"web","label":0}\n', encoding="utf-8")
            self.assertEqual(load_feedback(path), {"web": [1, 0]})


if __name__ == "__main__":
    unittest.main()
