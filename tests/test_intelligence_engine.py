"""Tests for the durable Intelligence Engine task queue."""
import tempfile
import unittest
from pathlib import Path

from crawler.intelligence_engine import (
    claim, connect, enqueue, fail, finish, initialize, summary,
)


class IntelligenceEngineQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.temp.name) / "queue.sqlite3")
        initialize(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_idempotent_enqueue_returns_same_task(self):
        first = enqueue(self.db, title="Index catalog", lane="catalog",
                        idempotency_key="catalog:source-a")
        second = enqueue(self.db, title="Index catalog", lane="catalog",
                         idempotency_key="catalog:source-a")
        self.assertEqual(first, second)
        self.assertEqual(summary(self.db)["counts"]["queued"], 1)

    def test_claim_requires_matching_lane_and_returns_lease(self):
        enqueue(self.db, title="Run quality checks", lane="quality")
        self.assertIsNone(claim(self.db, lane="crawler", worker="crawler-1"))
        task = claim(self.db, lane="quality", worker="quality-1")
        self.assertEqual(task["status"], "leased")
        self.assertEqual(task["lease_owner"], "quality-1")
        self.assertTrue(task["lease_token"])

    def test_only_lease_holder_can_complete(self):
        task_id = enqueue(self.db, title="Build index", lane="search")
        task = claim(self.db, lane="search", worker="search-1")
        with self.assertRaises(ValueError):
            finish(self.db, task_id=task_id, lease_token="wrong",
                   actor="search-2", result={"ok": True})
        finish(self.db, task_id=task_id, lease_token=task["lease_token"],
               actor="search-1", result={"verified": True})
        self.assertEqual(summary(self.db)["counts"]["succeeded"], 1)

    def test_failure_requeues_then_exhausts_retry_budget(self):
        task_id = enqueue(self.db, title="Probe catalog", lane="catalog",
                          max_attempts=2)
        first = claim(self.db, lane="catalog", worker="catalog-1")
        self.assertEqual(fail(self.db, task_id=task_id,
                              lease_token=first["lease_token"], actor="catalog-1",
                              error="temporary failure", retry_delay_seconds=0), "queued")
        second = claim(self.db, lane="catalog", worker="catalog-2")
        self.assertEqual(fail(self.db, task_id=task_id,
                              lease_token=second["lease_token"], actor="catalog-2",
                              error="second failure", retry_delay_seconds=0), "failed")
        self.assertEqual(summary(self.db)["counts"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
