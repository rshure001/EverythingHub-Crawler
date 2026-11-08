#!/usr/bin/env python3
"""Offline tests for acquisition scorecard accounting."""
import json
import tempfile
import unittest
from pathlib import Path

from acquisition_report import build_report


class AcquisitionReportTests(unittest.TestCase):
    def test_staging_is_not_misreported_as_indexed_or_searchable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            registry.write_text(json.dumps({
                "production_publishing": False,
                "connectors": [{
                    "source_id": "sample",
                    "name": "Sample",
                    "lane": "general",
                    "enabled": False,
                    "review_status": "not_reviewed",
                    "source_status": "proposed",
                    "counts": {"indexed": 0, "searchable": 0}
                }]
            }), encoding="utf-8")
            staging = root / "staging"
            staging.mkdir()
            (staging / "sample.jsonl").write_text(
                json.dumps({"source_id": "sample", "license": "CC0"}) + "\n",
                encoding="utf-8"
            )
            (staging / "sample.jsonl.checkpoint.json").write_text(json.dumps({
                "pages_committed": 1, "records_staged": 1,
                "records_rejected": 2, "complete": True
            }), encoding="utf-8")
            report = build_report(registry, staging)
            self.assertEqual(report["totals"]["records_staged"], 1)
            self.assertEqual(report["totals"]["records_rejected"], 2)
            self.assertEqual(report["totals"]["indexed"], 0)
            self.assertEqual(report["totals"]["searchable"], 0)
            self.assertFalse(report["production_publishing_enabled"])

    def test_empty_registry_produces_zero_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            registry.write_text('{"connectors": []}', encoding="utf-8")
            report = build_report(registry, root / "staging")
            self.assertEqual(report["totals"]["records_staged"], 0)
            self.assertEqual(report["sources"], [])


if __name__ == "__main__":
    unittest.main()
