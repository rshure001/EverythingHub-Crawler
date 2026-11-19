#!/usr/bin/env python3
"""Prevent stale crawler runs from refetching and replaying generated catalog files."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

SENSITIVE_PREFIXES = ("crawler/", "data/", "public-search/")
SENSITIVE_FILES = {".github/workflows/crawl.yml"}


def decide(changed_paths: Iterable[str], ref_name: str = "main") -> dict[str, object]:
    """Dispatch a fresh main-branch crawl if crawler inputs advanced after this run began."""
    paths = sorted({
        str(path).strip().replace("\\", "/")
        for path in changed_paths
        if str(path).strip()
    })
    if ref_name != "main":
        return {
            "action": "dispatch",
            "reason": "production crawler runs are restricted to the current main branch",
            "changed_paths": [],
        }

    relevant = [
        path for path in paths
        if path in SENSITIVE_FILES or path.startswith(SENSITIVE_PREFIXES)
    ]
    if relevant:
        return {
            "action": "dispatch",
            "reason": "crawler code, durable state, or public search artifacts advanced on main",
            "changed_paths": relevant,
        }
    return {
        "action": "continue",
        "reason": "no crawler inputs changed on main since this run's revision",
        "changed_paths": [],
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: crawl_revision_guard.py <changed-paths.txt> <ref-name>")
    paths = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    print(json.dumps(decide(paths, sys.argv[2]), sort_keys=True))


if __name__ == "__main__":
    main()
