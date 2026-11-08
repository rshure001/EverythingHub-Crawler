import unittest

from crawler.fleet import AcceptedIndex, FleetQueue, HostRateLimiter, LANES, State, lane_for


class FleetQueueTests(unittest.TestCase):
    def test_eight_independent_lanes(self):
        q = FleetQueue()
        self.assertEqual(len(q.lanes), 8)
        self.assertEqual(set(q.lanes), set(LANES))
        q.enqueue("https://www.nasa.gov/", lane="science")
        self.assertEqual(len(q.lanes["science"]), 1)
        self.assertEqual(sum(map(len, q.lanes.values())), 1)

    def test_duplicate_url_is_not_enqueued_twice(self):
        q = FleetQueue()
        self.assertTrue(q.enqueue("https://example.org/a"))
        self.assertFalse(q.enqueue("https://example.org/a"))
        self.assertEqual(len(q.jobs), 1)

    def test_lease_owner_required_and_completion(self):
        q = FleetQueue()
        q.enqueue("https://example.org/")
        job = q.claim("general", "worker-a", now=10)
        self.assertEqual(job.state, State.LEASED)
        with self.assertRaises(RuntimeError):
            q.complete(job.url, "worker-b")
        q.complete(job.url, "worker-a")
        self.assertEqual(q.jobs[job.url].state, State.DONE)

    def test_expired_lease_recovers(self):
        q = FleetQueue()
        q.enqueue("https://example.org/")
        job = q.claim("general", "worker-a", lease_seconds=2, now=10)
        self.assertEqual(q.recover_expired(now=13), 1)
        retry = q.claim("general", "worker-b", now=13)
        self.assertEqual(retry.url, job.url)
        self.assertEqual(retry.lease_owner, "worker-b")

    def test_retry_backoff_then_dead_letter(self):
        q = FleetQueue(max_attempts=2, base_backoff=3)
        q.enqueue("https://example.org/")
        job = q.claim("general", "w1", now=100)
        self.assertEqual(q.fail(job.url, "w1", "temporary", now=100), State.RETRY)
        self.assertIsNone(q.claim("general", "w2", now=102))
        job = q.claim("general", "w2", now=103)
        self.assertEqual(q.fail(job.url, "w2", "still failing", now=103), State.DEAD)
        self.assertEqual(list(q.dead_letters), [job.url])

    def test_host_limiter_is_shared_by_all_workers(self):
        limiter = HostRateLimiter(min_interval=2)
        a = limiter.reserve("https://example.org/a", now=10)
        b = limiter.reserve("https://example.org/b", now=10)
        self.assertEqual((a, b), (10, 12))
        self.assertEqual(limiter.reserve("https://other.org/", now=10), 10)

    def test_index_counts_unique_valid_records_only(self):
        index = AcceptedIndex()
        url = "https://example.org/page"
        self.assertFalse(index.accept(url, "A title", "short"))
        self.assertTrue(index.accept(url, "A title", "x" * 100))
        self.assertFalse(index.accept(url, "A title", "x" * 100))
        self.assertEqual(index.count(), 1)

    def test_lane_classifier_falls_back_to_general(self):
        self.assertEqual(lane_for("https://example.org/"), "general")
        self.assertIn(lane_for("https://science.nasa.gov/"), LANES)


if __name__ == "__main__":
    unittest.main()
