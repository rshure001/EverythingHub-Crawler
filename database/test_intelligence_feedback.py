import unittest
from pathlib import Path

from intelligence_feedback import (
    FEEDBACK_VERSION,
    MAX_BATCH_SIZE,
    validate_batch_size,
    validate_feedback,
    validate_profile_name,
)

FEATURES = {
    "relevance": 0.9,
    "source_quality": 0.7,
    "freshness": 0.5,
    "corroboration": 0.3,
}


class IntelligenceFeedbackValidationTests(unittest.TestCase):
    def test_valid_event_is_minimal_and_normalized(self):
        event = validate_feedback("helpful", FEATURES, document_id=12)
        self.assertEqual(event["document_id"], 12)
        self.assertEqual(event["feedback_label"], "helpful")
        self.assertEqual(event["feedback_version"], FEEDBACK_VERSION)
        self.assertEqual(event["feature_snapshot"], FEATURES)
        self.assertEqual(set(event), {
            "document_id", "feedback_label", "feature_snapshot", "feedback_version"
        })

    def test_document_reference_may_be_null(self):
        event = validate_feedback("verified", FEATURES)
        self.assertIsNone(event["document_id"])

    def test_rejects_unknown_labels_and_nonpositive_document_ids(self):
        with self.assertRaises(ValueError):
            validate_feedback("maybe", FEATURES)
        for document_id in (0, -1, True, "12"):
            with self.subTest(document_id=document_id):
                with self.assertRaises(ValueError):
                    validate_feedback("helpful", FEATURES, document_id=document_id)

    def test_rejects_missing_extra_and_non_numeric_features(self):
        for snapshot in (
            {"relevance": 1},
            {**FEATURES, "raw_comment": "not allowed"},
            {**FEATURES, "freshness": "0.5"},
            {**FEATURES, "freshness": True},
        ):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(ValueError):
                    validate_feedback("helpful", snapshot)

    def test_rejects_out_of_range_and_nonfinite_features(self):
        for value in (-0.01, 1.01, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                snapshot = {**FEATURES, "relevance": value}
                with self.assertRaises(ValueError):
                    validate_feedback("helpful", snapshot)

    def test_profile_names_are_bounded(self):
        self.assertEqual(validate_profile_name("default"), "default")
        self.assertEqual(validate_profile_name("hurricane_v2"), "hurricane_v2")
        for name in ("", "Default", "9profile", "has space", "x" * 65, "../public"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_profile_name(name)

    def test_batch_size_is_bounded(self):
        self.assertEqual(validate_batch_size(1), 1)
        self.assertEqual(validate_batch_size(20000), MAX_BATCH_SIZE)
        with self.assertRaises(ValueError):
            validate_batch_size(0)
        for invalid in (True, 1.5, "5"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_batch_size(invalid)

    def test_feedback_cursor_migration_is_additive_and_eh_quad_only(self):
        sql = (Path(__file__).with_name("004_eh_quad_feedback_cursor.sql")
               .read_text(encoding="utf-8").lower())
        self.assertIn("alter table eh_quad.eh_rank_profiles", sql)
        self.assertIn("add column if not exists last_feedback_id", sql)
        self.assertIn("check (last_feedback_id >= 0)", sql)
        self.assertNotIn("public.", sql)
        self.assertNotIn("drop ", sql)
        self.assertNotIn("delete from", sql)
        self.assertNotIn("truncate ", sql)


if __name__ == "__main__":
    unittest.main()
