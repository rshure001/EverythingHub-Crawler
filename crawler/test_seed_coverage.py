import unittest
from urllib.parse import urlsplit

from seed_coverage import DEFAULT_SEED_RETRY_SECONDS, schedule_missing_seed_urls


def host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class SeedCoverageTests(unittest.TestCase):
    def test_schedules_only_unrepresented_seed_hosts_and_sets_cooldown(self):
        seeds = ["https://covered.example/", "https://missing.example/"]
        retry_after = {}
        scheduled = schedule_missing_seed_urls(
            seeds,
            {"covered.example"},
            set(),
            retry_after,
            now=1_000,
            host_key=host_key,
        )
        self.assertEqual(scheduled, [("https://missing.example/", 0)])
        self.assertEqual(
            retry_after["https://missing.example/"],
            1_000 + DEFAULT_SEED_RETRY_SECONDS,
        )

    def test_represented_host_clears_stale_retry_state(self):
        seed = "https://covered.example/"
        retry_after = {seed: 9_999}
        scheduled = schedule_missing_seed_urls(
            [seed], {"covered.example"}, set(), retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [])
        self.assertNotIn(seed, retry_after)

    def test_existing_queued_seed_is_not_duplicated_and_gets_cooldown(self):
        seed = "https://missing.example/"
        retry_after = {}
        scheduled = schedule_missing_seed_urls(
            [seed], set(), {seed}, retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [])
        self.assertEqual(retry_after[seed], 1_000 + DEFAULT_SEED_RETRY_SECONDS)

    def test_respects_retry_cooldown(self):
        seed = "https://missing.example/"
        retry_after = {seed: 5_000}
        scheduled = schedule_missing_seed_urls(
            [seed], set(), set(), retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [])
        self.assertEqual(retry_after[seed], 1_000 + DEFAULT_SEED_RETRY_SECONDS)

    def test_old_long_cooldown_is_clamped_to_new_retry_window(self):
        seed = "https://missing.example/"
        retry_after = {seed: 90_000}
        scheduled = schedule_missing_seed_urls(
            [seed], set(), set(), retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [])
        self.assertEqual(retry_after[seed], 1_000 + DEFAULT_SEED_RETRY_SECONDS)

    def test_expired_seed_is_requeued_and_cooldown_advanced(self):
        seed = "https://missing.example/"
        retry_after = {seed: 900}
        scheduled = schedule_missing_seed_urls(
            [seed], set(), set(), retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [(seed, 0)])
        self.assertEqual(retry_after[seed], 1_000 + DEFAULT_SEED_RETRY_SECONDS)

    def test_queue_limit_prevents_additional_seed_work(self):
        seeds = ["https://one.example/", "https://two.example/"]
        retry_after = {}
        scheduled = schedule_missing_seed_urls(
            seeds, set(), {"https://already-queued.example/"},
            retry_after, now=1_000, queue_limit=2, host_key=host_key,
        )
        self.assertEqual(scheduled, [("https://one.example/", 0)])
        self.assertNotIn("https://two.example/", retry_after)

    def test_duplicate_seed_entries_are_enqueued_once(self):
        seed = "https://missing.example/"
        retry_after = {}
        scheduled = schedule_missing_seed_urls(
            [seed, seed], set(), set(), retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [(seed, 0)])

    def test_invalid_retry_timestamp_is_treated_as_due(self):
        seed = "https://missing.example/"
        retry_after = {seed: "not-a-timestamp"}
        scheduled = schedule_missing_seed_urls(
            [seed], set(), set(), retry_after,
            now=1_000, host_key=host_key,
        )
        self.assertEqual(scheduled, [(seed, 0)])
        self.assertEqual(retry_after[seed], 1_000 + DEFAULT_SEED_RETRY_SECONDS)


if __name__ == "__main__":
    unittest.main()
