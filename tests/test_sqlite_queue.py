import tempfile
import unittest
from pathlib import Path

from crawler.fleet import State
from crawler.sqlite_queue import SQLiteFleetQueue


class SQLiteFleetQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "fleet.sqlite3")
        self.queue = SQLiteFleetQueue(self.db_path, max_attempts=2, base_backoff=3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_survives_reopen(self):
        url = "https://science.nasa.gov/example"
        self.assertTrue(self.queue.enqueue(url))
        reopened = SQLiteFleetQueue(self.db_path)
        job = reopened.claim("science", "worker-1", now=100)
        self.assertEqual(job.url, url)
        self.assertEqual(reopened.get(url).state, State.LEASED)

    def test_atomic_claim_only_one_worker_gets_job(self):
        url = "https://example.org/item"
        self.queue.enqueue(url, lane="general")
        first = self.queue.claim("general", "worker-a", now=10)
        second = self.queue.claim("general", "worker-b", now=10)
        self.assertEqual(first.url, url)
        self.assertIsNone(second)

    def test_lease_recovery_after_restart(self):
        url = "https://example.org/lease"
        self.queue.enqueue(url, lane="general")
        self.queue.claim("general", "worker-a", lease_seconds=2, now=10)
        reopened = SQLiteFleetQueue(self.db_path)
        self.assertEqual(reopened.recover_expired(now=13), 1)
        job = reopened.claim("general", "worker-b", now=13)
        self.assertEqual(job.lease_owner, "worker-b")

    def test_retry_backoff_and_dead_letter_persist(self):
        url = "https://example.org/retry"
        self.queue.enqueue(url, lane="general")
        job = self.queue.claim("general", "worker-a", now=100)
        self.assertEqual(self.queue.fail(url, "worker-a", "network", now=100), State.RETRY)
        self.assertIsNone(self.queue.claim("general", "worker-b", now=102))
        job = self.queue.claim("general", "worker-b", now=103)
        self.assertEqual(self.queue.fail(url, "worker-b", "again", now=103), State.DEAD)
        self.assertEqual(self.queue.counts()["dead_letters"], 1)
        self.assertEqual(self.queue.get(url).state, State.DEAD)

    def test_duplicate_enqueue_is_idempotent(self):
        url = "https://example.org/duplicate"
        self.assertTrue(self.queue.enqueue(url))
        self.assertFalse(self.queue.enqueue(url))
        self.assertEqual(self.queue.counts()["queued"], 1)


if __name__ == "__main__":
    unittest.main()
