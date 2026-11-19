#!/usr/bin/env python3
"""Resolve a release directory and enforce the canonical release-root boundary."""
from pathlib import Path
import sys


def resolve_release_dir(release_dir: str, release_root: str | Path = "/srv/everythinghub/releases") -> Path:
    root = Path(release_root).resolve(strict=True)
    candidate = Path(release_dir).resolve(strict=True)
    if candidate == root:
        raise ValueError("the release root itself is not a publishable release")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("release path resolves outside /srv/everythinghub/releases") from exc
    if not candidate.is_dir() or not (candidate / "index.html").is_file():
        raise ValueError("release must be a directory containing index.html")
    return candidate


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: resolve_release.py /srv/everythinghub/releases/<release-id>", file=sys.stderr)
        return 2
    try:
        print(resolve_release_dir(sys.argv[1]))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Refusing release: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
