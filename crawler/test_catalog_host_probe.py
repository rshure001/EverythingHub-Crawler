import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import catalog_host_probe


class CumulativeProbeTests(unittest.TestCase):
    def test_batches_accumulate_host_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "hosts.json"
            output = root / "probes.json"
            source.write_text(json.dumps({
                "source_index_pages": 2,
                "host_candidates": [
                    {"host": "a.example", "source_pages": 1, "hint_counts": {"api": 1}},
                    {"host": "b.example", "source_pages": 1, "hint_counts": {"catalog": 1}},
                ],
            }), encoding="utf-8")

            def fake_probe(host, kind, path):
                return {"kind": kind, "url": "https://" + host + path,
                        "status": "responded", "evidence": "test_interface"}

            with patch.object(catalog_host_probe, "probe_one", side_effect=fake_probe), \
                 patch.object(catalog_host_probe.time, "sleep"):
                catalog_host_probe.run(source, output, limit=1, offset=0, pause=0)
                result = catalog_host_probe.run(source, output, limit=1, offset=1, pause=0)

            self.assertEqual(result["hosts_probed"], 2)
            self.assertEqual(result["verified_interface_hosts"], 2)
            self.assertEqual(result["verified_interfaces"], 10)
            self.assertEqual({x["host"] for x in result["results"]},
                             {"a.example", "b.example"})


if __name__ == "__main__":
    unittest.main()
