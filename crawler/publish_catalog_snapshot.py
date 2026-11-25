#!/usr/bin/env python3
"""Publish a candidate catalog snapshot after exhaustive integrity validation.

The caller must run catalog_page_analysis.py --validate-only successfully
before invoking this publisher. This publisher independently checks every
artifact checksum and accepts only candidate-staging records that remain
explicitly unverified and commercially ineligible. The snapshot remains tied
to the immutable source-index SHA in report.json.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command, cwd: Path, env=None, check=True, timeout: float = 600):
    """Run a publication command with a finite timeout and actionable failure."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        rendered = " ".join(map(str, command))
        raise RuntimeError(
            f"command timed out after {timeout:g}s: {rendered}"
        ) from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(map(str, command))}: {detail[-2000:]}"
        )
    return result


def _git(root: Path, *args, env=None, check=True):
    return _run(["git", *args], cwd=root, env=env, check=check)


def _is_ancestor(root: Path, ancestor: str, descendant: str, env=None) -> bool:
    return _git(
        root, "merge-base", "--is-ancestor", ancestor, descendant, env=env, check=False
    ).returncode == 0


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("catalog report generated_at timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("catalog report generated_at must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("catalog report generated_at must include a timezone")
    return parsed


def _read_snapshot(directory: Path) -> dict:
    required = (
        "manifest.json",
        "report.json",
        "registry.json.gz",
    )
    for name in required:
        if not (directory / name).is_file():
            raise ValueError(f"validated catalog snapshot is missing {name}")
    if not (directory / "indexes").is_dir():
        raise ValueError("validated catalog snapshot is missing per-catalog indexes")
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest_files = manifest.get("files")
    if isinstance(manifest_files, list) and manifest_files:
        names = {item.get("file") for item in manifest_files if isinstance(item, dict)}
        has_records = "records.json.gz" in names or any(
            isinstance(name, str) and name.startswith("records/") for name in names
        )
        has_record_identities = "record-catalogs.jsonl.gz" in names or any(
            isinstance(name, str) and name.startswith("record-catalogs/") for name in names
        )
        if not has_records or not has_record_identities:
            raise ValueError("catalog snapshot is missing global record or record-identity artifacts")
        for entry in manifest_files:
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                raise ValueError("catalog snapshot file manifest entry is malformed")
            artifact = directory / entry["file"]
            if not artifact.is_file():
                raise ValueError("catalog snapshot artifact is missing: " + entry["file"])
            raw = artifact.read_bytes()
            if len(raw) >= 100_000_000:
                raise ValueError("catalog snapshot artifact exceeds GitHub's per-file limit: " + entry["file"])
            if len(raw) != entry.get("bytes") or hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                raise ValueError("catalog snapshot artifact integrity mismatch: " + entry["file"])
    try:
        registry = json.loads(gzip.decompress((directory / "registry.json.gz").read_bytes()).decode("utf-8"))
    except (OSError, ValueError, gzip.BadGzipFile, UnicodeDecodeError) as exc:
        raise ValueError("candidate catalog registry is unreadable") from exc
    catalogs = registry.get("catalogs") if isinstance(registry, dict) else None
    if not isinstance(registry, dict) or registry.get("status") != "candidate_staging_not_production" or not isinstance(catalogs, list):
        raise ValueError("only a candidate-staging catalog registry may be published")
    if any(
        not isinstance(item, dict)
        or item.get("verified") is not False
        or item.get("commercial_eligible") is not False
        or item.get("source_terms_status") != "not_reviewed"
        or item.get("record_level_clearance") not in (None, "not_reviewed")
        for item in catalogs
    ):
        raise ValueError("candidate registry contains a catalog that overstates verification or rights clearance")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("catalog report metrics must be an object")
    if metrics.get("unique_catalogs_discovered") != len(catalogs):
        raise ValueError("candidate registry catalog count does not match the analysis report")
    source_pages = report.get("source_index_pages")
    if isinstance(source_pages, bool) or not isinstance(source_pages, int) or source_pages < 0:
        raise ValueError("catalog report source_index_pages must be a non-negative integer")
    source_sha = report.get("source_index_sha256")
    if (
        not isinstance(source_sha, str)
        or len(source_sha) != 64
        or any(char not in "0123456789abcdef" for char in source_sha.lower())
    ):
        raise ValueError("catalog report source_index_sha256 must be a SHA-256 digest")
    generated_at = report.get("generated_at")
    generated_time = _parse_timestamp(generated_at)
    metric_keys = (
        "unique_source_pages",
        "eligible_pages",
        "unique_catalogs_discovered",
        "duplicate_catalog_candidates",
        "unique_records",
        "records_published",
        "media_references",
    )
    counts = {}
    for key in metric_keys:
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"catalog report metric {key} must be a non-negative integer")
        counts[key] = value
    return {
        "report": report,
        "generated_at": generated_at,
        "generated_time": generated_time,
        "source_index_pages": source_pages,
        "source_index_sha256": source_sha.lower(),
        "analyzed_unique_pages": counts["unique_source_pages"],
        "eligible_pages": counts["eligible_pages"],
        "catalogs": counts["unique_catalogs_discovered"],
        "duplicate_catalog_candidates": counts["duplicate_catalog_candidates"],
        "records": counts["unique_records"],
        "records_published": counts["records_published"],
        "media_references": counts["media_references"],
    }


def _current_report(root: Path):
    path = root / "data" / "page-derived-catalogs" / "report.json"
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "generated_at": report.get("generated_at"),
        "generated_time": _parse_timestamp(report.get("generated_at")),
        "source_index_sha256": report.get("source_index_sha256"),
        "source_index_pages": report.get("source_index_pages"),
    }


def _is_superseded(current, snapshot) -> bool:
    if current is None:
        return False
    if current["generated_time"] > snapshot["generated_time"]:
        return True
    if current["generated_time"] == snapshot["generated_time"]:
        return current.get("source_index_sha256") != snapshot["source_index_sha256"]
    return False


def _current_index_snapshot(root: Path):
    path = root / "public-search" / "manifest.json"
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    value = manifest.get("total_pages")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return {"pages": value, "sha256": hashlib.sha256(raw).hexdigest()}


def publish_snapshot(
    repo_root=ROOT,
    attempts: int = 4,
    ledger_command=None,
    env=None,
) -> dict:
    """Publish candidate catalog files on the latest main tip, without reanalysis."""
    root = Path(repo_root).resolve()
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 8:
        raise ValueError("attempts must be an integer from 1 to 8")
    source = root / "data" / "page-derived-catalogs"
    snapshot = _read_snapshot(source)
    original_base = _git(root, "rev-parse", "HEAD").stdout.strip()
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    ledger = ledger_command or [
        sys.executable,
        str(root / "project" / "ledger.py"),
        "--record-page-derived-catalog-analysis",
    ]

    with tempfile.TemporaryDirectory(prefix="eh-catalog-publish-") as temporary:
        saved = Path(temporary) / "page-derived-catalogs"
        shutil.copytree(source, saved)
        for attempt in range(1, attempts + 1):
            _git(root, "fetch", "origin", "main", env=child_env)
            remote_base = _git(root, "rev-parse", "origin/main", env=child_env).stdout.strip()
            if remote_base != original_base and not _is_ancestor(
                root, original_base, remote_base, env=child_env
            ):
                raise RuntimeError("remote main does not descend from the analyzed base; refusing reset")

            _git(root, "reset", "--hard", "origin/main", env=child_env)
            current = _current_report(root)
            if _is_superseded(current, snapshot):
                return {
                    **{key: value for key, value in snapshot.items() if key != "report" and key != "generated_time"},
                    "published": False,
                    "superseded": True,
                    "attempts_used": attempt,
                    "current_report_generated_at": current["generated_at"],
                }

            target = root / "data" / "page-derived-catalogs"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(saved, target)

            _run(ledger, cwd=root, env=child_env)
            _git(root, "config", "user.name", "github-actions[bot]", env=child_env)
            _git(
                root,
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
                env=child_env,
            )
            _git(root, "add", "-f", "data/page-derived-catalogs", "data/project-ledger.json", env=child_env)
            staged = _git(root, "diff", "--cached", "--quiet", env=child_env, check=False)
            if staged.returncode == 0:
                current_index = _current_index_snapshot(root)
                return {
                    **{key: value for key, value in snapshot.items() if key != "report" and key != "generated_time"},
                    "published": False,
                    "already_current": True,
                    "attempts_used": attempt,
                    "current_index_pages": current_index["pages"] if current_index else None,
                    "current_index_sha256": current_index["sha256"] if current_index else None,
                    "stale_against_current_index": (
                        current_index is not None and current_index["sha256"] != snapshot["source_index_sha256"]
                    ),
                }
            if staged.returncode != 1:
                raise RuntimeError("unable to inspect staged catalog publication changes")

            _git(root, "commit", "-m", "Publish candidate page-derived catalog snapshot", env=child_env)
            local_head = _git(root, "rev-parse", "HEAD", env=child_env).stdout.strip()
            pushed = _git(root, "push", "origin", "HEAD:main", env=child_env, check=False)
            if pushed.returncode == 0:
                current_index = _current_index_snapshot(root)
                return {
                    **{key: value for key, value in snapshot.items() if key != "report" and key != "generated_time"},
                    "published": True,
                    "attempts_used": attempt,
                    "commit": local_head,
                    "current_index_pages": current_index["pages"] if current_index else None,
                    "current_index_sha256": current_index["sha256"] if current_index else None,
                    "stale_against_current_index": (
                        current_index is not None and current_index["sha256"] != snapshot["source_index_sha256"]
                    ),
                }

            _git(root, "fetch", "origin", "main", env=child_env)
            remote_after = _git(root, "rev-parse", "origin/main", env=child_env).stdout.strip()
            if remote_after == local_head:
                current_index = _current_index_snapshot(root)
                return {
                    **{key: value for key, value in snapshot.items() if key != "report" and key != "generated_time"},
                    "published": True,
                    "attempts_used": attempt,
                    "commit": local_head,
                    "already_present": True,
                    "current_index_pages": current_index["pages"] if current_index else None,
                    "current_index_sha256": current_index["sha256"] if current_index else None,
                    "stale_against_current_index": (
                        current_index is not None and current_index["sha256"] != snapshot["source_index_sha256"]
                    ),
                }
            if remote_after == remote_base:
                detail = (pushed.stderr or pushed.stdout or "").strip()
                raise RuntimeError(
                    "catalog push failed without a main-branch advance; refusing blind retry: "
                    + detail[-1200:]
                )
            if not _is_ancestor(root, remote_base, remote_after, env=child_env):
                raise RuntimeError("remote main changed non-fast-forward; refusing automatic reset")
            if attempt == attempts:
                raise RuntimeError("main advanced during every bounded catalog publication attempt")
            # Retry only the publish/ledger operation. The validated snapshot is
            # retained in the temporary directory and is never re-analyzed here.

    raise RuntimeError("catalog snapshot publication exhausted its bounded attempts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--attempts", type=int, default=4)
    args = parser.parse_args()
    result = publish_snapshot(args.repo_root, attempts=args.attempts)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
