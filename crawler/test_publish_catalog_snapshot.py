from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from publish_catalog_snapshot import publish_snapshot


def run(command, cwd=None, env=None):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def report(generated_at: str, source_pages: int, source_sha: str):
    return {
        "generated_at": generated_at,
        "source_index_pages": source_pages,
        "source_index_sha256": source_sha,
        "metrics": {
            "unique_source_pages": source_pages,
            "eligible_pages": max(0, source_pages - 1),
            "unique_catalogs_discovered": 2,
            "duplicate_catalog_candidates": 4,
            "unique_records": 3,
            "records_published": 0,
            "media_references": 1,
        },
    }


class CatalogSnapshotPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="eh-publish-test-")
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.first = self.root / "first"
        self.second = self.root / "second"
        self.fake_ledger = self.root / "fake_ledger.py"
        self.hook_marker = self.root / "pre-push-ran"

        run(["git", "init", "--bare", "--initial-branch=main", str(self.remote)])
        run(["git", "init", "--initial-branch=main", str(self.seed)])
        run(["git", "config", "user.name", "Catalog Test"], cwd=self.seed)
        run(["git", "config", "user.email", "catalog-test@example.invalid"], cwd=self.seed)

        write_json(self.seed / "public-search" / "manifest.json", {"total_pages": 10})
        write_json(
            self.seed / "data" / "page-derived-catalogs" / "report.json",
            report("2026-11-22T07:00:00Z", 8, "b" * 64),
        )
        write_json(self.seed / "data" / "page-derived-catalogs" / "manifest.json", {"version": 1})
        (self.seed / "data" / "page-derived-catalogs" / "registry.json.gz").parent.mkdir(parents=True, exist_ok=True)
        (self.seed / "data" / "page-derived-catalogs" / "registry.json.gz").write_bytes(b"old-registry")
        (self.seed / "data" / "page-derived-catalogs" / "records.json.gz").write_bytes(b"old-records")
        (self.seed / "data" / "page-derived-catalogs" / "indexes").mkdir(parents=True, exist_ok=True)
        (self.seed / "data" / "page-derived-catalogs" / "indexes" / "old.json.gz").write_bytes(b"old-index")
        (self.seed / "data" / "host-catalog-candidates.json").write_text("old-hosts\n", encoding="utf-8")
        (self.seed / "data" / "page-catalog-seeds.json.gz").write_bytes(b"old-seeds")
        write_json(self.seed / "data" / "project-ledger.json", {
            "schema_version": 1, "project": "Everything Hub", "events": []
        })
        run(["git", "add", "."], cwd=self.seed)
        run(["git", "commit", "-m", "Initial corpus snapshot"], cwd=self.seed)
        run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.seed)
        run(["git", "push", "-u", "origin", "main"], cwd=self.seed)
        run(["git", "clone", str(self.remote), str(self.first)])
        run(["git", "clone", str(self.remote), str(self.second)])
        for directory in (self.first, self.second):
            run(["git", "config", "user.name", "Catalog Test"], cwd=directory)
            run(["git", "config", "user.email", "catalog-test@example.invalid"], cwd=directory)

        self.snapshot_dir = self.first / "data" / "page-derived-catalogs"
        write_json(self.snapshot_dir / "report.json", report("2026-11-22T12:00:00Z", 10, "a" * 64))
        write_json(self.snapshot_dir / "manifest.json", {"version": 1, "validated": True})
        (self.snapshot_dir / "registry.json.gz").write_bytes(b"new-registry")
        (self.snapshot_dir / "records.json.gz").write_bytes(b"new-records")
        (self.snapshot_dir / "indexes" / "catalog-1.json.gz").parent.mkdir(parents=True, exist_ok=True)
        (self.snapshot_dir / "indexes" / "catalog-1.json.gz").write_bytes(b"new-index")
        (self.snapshot_dir / "source-host-review-queue.json").write_text("[]\n", encoding="utf-8")

        self.fake_ledger.write_text(
            "import json\n"
            "from pathlib import Path\n"
            "report = json.loads(Path('data/page-derived-catalogs/report.json').read_text())\n"
            "path = Path('data/project-ledger.json')\n"
            "ledger = json.loads(path.read_text())\n"
            "ledger['events'].append({'id': 'page-derived-catalog-analysis-test', "
            "'source_index_pages': report['source_index_pages'], "
            "'source_index_sha256': report['source_index_sha256']})\n"
            "path.write_text(json.dumps(ledger, sort_keys=True) + '\\n')\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_retries_only_publication_after_concurrent_crawler_commit(self):
        hook = self.first / ".git" / "hooks" / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if [ ! -f \"$EH_HOOK_MARKER\" ]; then\n"
            "  touch \"$EH_HOOK_MARKER\"\n"
            "  cd \"$EH_SECOND_CLONE\"\n"
            "  python3 - <<'PY'\n"
            "import json\n"
            "from pathlib import Path\n"
            "path = Path('public-search/manifest.json')\n"
            "value = json.loads(path.read_text())\n"
            "value['total_pages'] = 12\n"
            "path.write_text(json.dumps(value, sort_keys=True) + '\\n')\n"
            "Path('data/host-catalog-candidates.json').write_text('new-hosts\\n')\n"
            "Path('data/page-catalog-seeds.json.gz').write_bytes(b'new-seeds')\n"
            "PY\n"
            "  git add public-search/manifest.json data/host-catalog-candidates.json data/page-catalog-seeds.json.gz\n"
            "  git commit -m 'Concurrent crawler index refresh'\n"
            "  git push origin main\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        environment = {
            "EH_SECOND_CLONE": str(self.second),
            "EH_HOOK_MARKER": str(self.hook_marker),
        }
        result = publish_snapshot(
            self.first,
            attempts=4,
            ledger_command=[sys.executable, str(self.fake_ledger)],
            env=environment,
        )

        self.assertTrue(result["published"])
        self.assertEqual(result["attempts_used"], 2)
        self.assertEqual(result["analyzed_unique_pages"], 10)
        self.assertEqual(result["current_index_pages"], 12)
        self.assertTrue(result["stale_against_current_index"])

        verify = self.root / "verify"
        run(["git", "clone", str(self.remote), str(verify)])
        manifest = json.loads((verify / "public-search" / "manifest.json").read_text())
        catalog_report = json.loads((verify / "data" / "page-derived-catalogs" / "report.json").read_text())
        ledger = json.loads((verify / "data" / "project-ledger.json").read_text())

        self.assertEqual(manifest["total_pages"], 12)
        self.assertEqual(catalog_report["source_index_pages"], 10)
        self.assertEqual(catalog_report["source_index_sha256"], "a" * 64)
        self.assertEqual(
            (verify / "data" / "host-catalog-candidates.json").read_text(),
            "new-hosts\n",
        )
        self.assertEqual((verify / "data" / "page-catalog-seeds.json.gz").read_bytes(), b"new-seeds")
        self.assertEqual(len(ledger["events"]), 1)
        self.assertEqual(ledger["events"][0]["source_index_pages"], 10)
        self.assertEqual(ledger["events"][0]["source_index_sha256"], "a" * 64)

    def test_newer_catalog_snapshot_on_main_is_never_overwritten(self):
        current_report = report("2026-11-22T13:00:00Z", 12, "c" * 64)
        write_json(self.seed / "data" / "page-derived-catalogs" / "report.json", current_report)
        run(["git", "add", "data/page-derived-catalogs/report.json"], cwd=self.seed)
        run(["git", "commit", "-m", "Newer catalog snapshot"], cwd=self.seed)
        run(["git", "push", "origin", "main"], cwd=self.seed)
        run(["git", "fetch", "origin", "main"], cwd=self.first)

        result = publish_snapshot(
            self.first,
            attempts=2,
            ledger_command=[sys.executable, str(self.fake_ledger)],
        )
        self.assertFalse(result["published"])
        self.assertTrue(result["superseded"])
        self.assertEqual(result["current_report_generated_at"], "2026-11-22T13:00:00Z")

        verify = self.root / "verify-newer"
        run(["git", "clone", str(self.remote), str(verify)])
        current = json.loads((verify / "data" / "page-derived-catalogs" / "report.json").read_text())
        self.assertEqual(current["source_index_sha256"], "c" * 64)


if __name__ == "__main__":
    unittest.main()
