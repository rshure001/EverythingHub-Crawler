import io
import json
import urllib.error
import urllib.request
import tempfile
import unittest
from pathlib import Path

from contact_page_pilot import SameOriginRedirectHandler, run, validate_target


class ContactPagePilotTests(unittest.TestCase):
    def setUp(self):
        self.allowed = {"veterans.nv.gov", "vegasveteranvoices.com"}
        self.targets = [
            {"organization": "NDVS", "url": "https://veterans.nv.gov/contact/", "source_type": "government_contact_page"},
            {"organization": "Vegas Veteran Voices", "url": "https://vegasveteranvoices.com/contact/", "source_type": "organization_contact_page"},
        ]

    def test_only_https_allowlisted_hosts_are_accepted(self):
        with self.assertRaises(ValueError):
            validate_target({"url": "http://veterans.nv.gov/contact/"}, self.allowed)
        with self.assertRaises(ValueError):
            validate_target({"url": "https://evil.example/contact/"}, self.allowed)
        with self.assertRaises(ValueError):
            validate_target({"url": "https://user:pass@veterans.nv.gov/contact/"}, self.allowed)

    def test_query_and_fragment_targets_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_target({"url": "https://veterans.nv.gov/contact/?q=x"}, self.allowed)
        with self.assertRaises(ValueError):
            validate_target({"url": "https://veterans.nv.gov/contact/#form"}, self.allowed)

    def test_pilot_is_capped_at_five_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "pilot_id": "test",
                "max_pages": 5,
                "allowed_hosts": ["veterans.nv.gov"],
                "targets": [
                    {"organization": str(i), "url": f"https://veterans.nv.gov/contact/{i}"}
                    for i in range(6)
                ],
            }
            path = root / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "capped at five"):
                run(path, root / "out.jsonl", lambda *_: {"status": "fetched"})

    def test_staging_only_results_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "pilot_id": "test",
                "max_pages": 2,
                "allowed_hosts": ["veterans.nv.gov", "vegasveteranvoices.com"],
                "targets": self.targets,
            }
            path = root / "config.json"
            output = root / "out.jsonl"
            path.write_text(json.dumps(config), encoding="utf-8")
            calls = []
            def fake_fetch(target, allowed):
                calls.append(target["url"])
                return {"status": "fetched", "title": target["organization"]}
            summary = run(path, output, fake_fetch)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary["targets"], 2)
            self.assertEqual(summary["fetched"], 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(row["production_published"] is False for row in rows))
            self.assertEqual(summary["production_published"], 0)


    def test_redirects_must_stay_same_origin_and_obey_robots(self):
        origin = ("https", "veterans.nv.gov", 443)
        request = urllib.request.Request("https://veterans.nv.gov/contact/")
        allowed = SameOriginRedirectHandler(origin, is_allowed=lambda url: url.endswith("/allowed"))
        result = allowed.redirect_request(
            request, io.BytesIO(), 302, "Found", {}, "https://veterans.nv.gov/allowed"
        )
        self.assertEqual(result.full_url, "https://veterans.nv.gov/allowed")
        with self.assertRaisesRegex(urllib.error.HTTPError, "robots.txt"):
            allowed.redirect_request(
                request, io.BytesIO(), 302, "Found", {}, "https://veterans.nv.gov/blocked"
            )
        with self.assertRaisesRegex(urllib.error.HTTPError, "cross-origin"):
            allowed.redirect_request(
                request, io.BytesIO(), 302, "Found", {}, "https://other.example/allowed"
            )

    def test_duplicate_urls_are_processed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self.targets[0]
            config = {"pilot_id": "test", "max_pages": 5,
                      "allowed_hosts": ["veterans.nv.gov"],
                      "targets": [target, dict(target)]}
            path = root / "config.json"
            output = root / "out.jsonl"
            path.write_text(json.dumps(config), encoding="utf-8")
            calls = []
            summary = run(path, output, lambda target, allowed: calls.append(target["url"]) or {"status": "fetched"})
            self.assertEqual(summary["targets"], 1)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
