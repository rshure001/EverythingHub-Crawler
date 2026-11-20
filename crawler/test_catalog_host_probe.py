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

            approved = {"discovery_allowed": True, "allowed_probe_paths": ["/api/3/action/package_search"]}
            with patch.object(catalog_host_probe, "policy_for_host", return_value=approved), \
                 patch.object(catalog_host_probe, "probe_one", side_effect=fake_probe), \
                 patch.object(catalog_host_probe.time, "sleep"):
                catalog_host_probe.run(source, output, limit=1, offset=0, pause=0)
                result = catalog_host_probe.run(source, output, limit=1, offset=1, pause=0)

            self.assertEqual(result["hosts_probed"], 2)
            self.assertEqual(result["verified_interface_hosts"], 2)
            self.assertEqual(result["verified_interfaces"], 2)
            self.assertEqual({x["host"] for x in result["results"]},
                             {"a.example", "b.example"})


    def test_unreviewed_host_is_not_requested(self):
        with patch.object(catalog_host_probe, "policy_for_host", return_value=None), \
             patch.object(catalog_host_probe, "request_source_bytes") as request:
            result = catalog_host_probe.probe_one("unknown.example", "ckan_api", "/api/3/action/package_search")
        self.assertEqual(result["status"], "source_terms_unreviewed")
        request.assert_not_called()

    def test_robots_denial_is_reported_without_following_request(self):
        from catalog_source_policy import SourcePolicyError
        with patch.object(catalog_host_probe, "policy_for_host", return_value={"discovery_allowed": True}), \
             patch.object(catalog_host_probe, "request_source_bytes", side_effect=SourcePolicyError("robots.txt disallows")):
            result = catalog_host_probe.probe_one("catalog.data.gov", "ckan_api", "/api/3/action/package_search")
        self.assertEqual(result["status"], "source_policy_blocked")

if __name__ == "__main__":
    unittest.main()
