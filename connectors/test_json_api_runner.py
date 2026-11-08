#!/usr/bin/env python3
"""Tests for the policy-gated API acquisition runner; all network calls are mocked."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import json_api_runner as runner


class ConnectorRunnerTests(unittest.TestCase):
    def config(self, root, approved=True):
        source = {
            "source_id": "test-source",
            "name": "Test source",
            "review_status": "reviewed_allowed" if approved else "not_reviewed",
            "source_status": "active",
            "enabled": True,
            "terms_url": "https://example.org/terms",
            "attribution_text": "Test source",
            "request_policy": {"min_delay_ms": 0},
            "api": {
                "endpoint": "https://api.example.org/items",
                "records_path": "data.items",
                "next_cursor_path": "meta.next",
                "cursor_param": "cursor",
                "page_size_param": "limit",
                "page_size": 2
            }
        }
        path = root / "registry.json"
        path.write_text(json.dumps({"connectors": [source]}), encoding="utf-8")
        return path

    def test_unreviewed_source_makes_no_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root, approved=False)
            with patch.object(runner, "fetch_page") as fetch:
                with self.assertRaisesRegex(ValueError, "not approved"):
                    runner.acquire(config, "test-source", root / "out.jsonl", 1)
                fetch.assert_not_called()

    def test_paginates_and_stages_source_provenance(self):
        pages = [
            {"data": {"items": [{"id": "a"}, {"id": "b"}]}, "meta": {"next": "page-2"}},
            {"data": {"items": [{"id": "c"}]}, "meta": {"next": None}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)
            output = root / "staged.jsonl"
            with patch.object(runner, "fetch_page", side_effect=pages) as fetch:
                count = runner.acquire(config, "test-source", output, 5)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(count, 3)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual([row["source_record"]["id"] for row in rows], ["a", "b", "c"])
            self.assertTrue(all(row["source_id"] == "test-source" for row in rows))
            self.assertTrue(all(row["attribution"] == "Test source" for row in rows))

    def test_resumes_from_durable_page_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)
            output = root / "staged.jsonl"
            first = {"data": {"items": [{"id": "first"}]}, "meta": {"next": "cursor-2"}}
            second = {"data": {"items": [{"id": "second"}]}, "meta": {"next": None}}
            with patch.object(runner, "fetch_page", return_value=first) as fetch:
                self.assertEqual(runner.acquire(config, "test-source", output, 1), 1)
                fetch.assert_called_once()
            with patch.object(runner, "fetch_page", return_value=second) as fetch:
                self.assertEqual(runner.acquire(config, "test-source", output, 1), 1)
                self.assertIn("cursor=cursor-2", fetch.call_args.args[0])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["source_record"]["id"] for row in rows], ["first", "second"])

    def test_unknown_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)
            with self.assertRaisesRegex(ValueError, "unknown connector"):
                runner.acquire(config, "missing", root / "out.jsonl", 1)


if __name__ == "__main__":
    unittest.main()
