#!/usr/bin/env python3
"""Tests for immutable crawl batches and deterministic page reduction."""
import json
import tempfile
import unittest
from pathlib import Path

from crawl_batches import (
    load_and_reduce, read_batch, reduce_page_batches, validate_batch, write_batch,
)


def page(url, title, fetched_at):
    return {"url": url, "title": title, "fetched_at": fetched_at}


class CrawlBatchTests(unittest.TestCase):
    def test_write_read_round_trip_and_integrity_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-01.json"
            result = write_batch(
                path, run_id="run-7", worker_id="worker-01",
                pages=[page("https://example.org/a", "A", 10)],
            )
            loaded = read_batch(path)
            self.assertEqual(loaded["run_id"], "run-7")
            self.assertEqual(loaded["pages"][0]["title"], "A")
            self.assertEqual(result["page_count"], 1)
            self.assertEqual(len(result["sha256"]), 64)

    def test_invalid_batch_rejected_as_a_unit(self):
        with self.assertRaisesRegex(ValueError, "invalid HTTPS URL"):
            validate_batch({
                "schema_version": 1, "run_id": "r", "worker_id": "w",
                "pages": [page("http://example.org/", "not https", 1)],
            })

    def test_rejects_nonfinite_timestamp(self):
        with self.assertRaisesRegex(ValueError, "invalid fetched_at"):
            validate_batch({
                "schema_version": 1, "run_id": "r", "worker_id": "w",
                "pages": [page("https://example.org/", "bad", float("nan"))],
            })

    def test_newer_record_wins_for_same_url(self):
        result = reduce_page_batches(
            [page("https://example.org/a", "old", 1)],
            [validate_batch({
                "schema_version": 1, "run_id": "r", "worker_id": "w",
                "pages": [page("https://example.org/a", "new", 2)],
            })],
        )
        self.assertEqual(result["pages"][0]["title"], "new")
        self.assertEqual(result["metrics"]["replaced_existing"], 1)

    def test_tie_break_is_independent_of_batch_order(self):
        first = validate_batch({
            "schema_version": 1, "run_id": "r", "worker_id": "a",
            "pages": [page("https://example.org/a", "Alpha", 5)],
        })
        second = validate_batch({
            "schema_version": 1, "run_id": "r", "worker_id": "b",
            "pages": [page("https://example.org/a", "Beta", 5)],
        })
        a = reduce_page_batches([], [first, second])
        b = reduce_page_batches([], [second, first])
        self.assertEqual(a, b)

    def test_replay_is_idempotent(self):
        batch = validate_batch({
            "schema_version": 1, "run_id": "r", "worker_id": "w",
            "pages": [page("https://example.org/a", "A", 5)],
        })
        once = reduce_page_batches([], [batch])
        twice = reduce_page_batches(once["pages"], [batch])
        self.assertEqual(once["pages"], twice["pages"])
        self.assertEqual(twice["metrics"]["unique_pages"], 1)

    def test_distinct_pages_are_sorted_and_deduplicated(self):
        batch = validate_batch({
            "schema_version": 1, "run_id": "r", "worker_id": "w",
            "pages": [
                page("https://example.org/z", "Z", 1),
                page("https://example.org/a", "A", 1),
                page("https://example.org/z", "Z newer", 2),
            ],
        })
        result = reduce_page_batches([], [batch])
        self.assertEqual([p["url"] for p in result["pages"]], [
            "https://example.org/a", "https://example.org/z",
        ])
        self.assertEqual(result["metrics"]["duplicate_occurrences"], 1)

    def test_load_and_reduce_uses_batches_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_batch(root / "b.json", run_id="r", worker_id="b",
                        pages=[page("https://example.org/b", "B", 1)])
            write_batch(root / "a.json", run_id="r", worker_id="a",
                        pages=[page("https://example.org/a", "A", 1)])
            result = load_and_reduce([], [root / "b.json", root / "a.json"])
            self.assertEqual(result["metrics"]["worker_count"], 2)
            self.assertEqual(len(result["pages"]), 2)

    def test_rejects_credentials_and_local_hosts(self):
        for url in ("https://user:pass@example.org/", "https://localhost/",
                    "https://service.internal/"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_batch({
                        "schema_version": 1, "run_id": "r", "worker_id": "w",
                        "pages": [page(url, "bad", 1)],
                    })


if __name__ == "__main__":
    unittest.main()
