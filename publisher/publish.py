#!/usr/bin/env python3
"""Validate a deterministic, manifest-pinned static release for EverythingHub."""
from __future__ import annotations
import argparse, hashlib, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
MANIFEST = SITE / "release-manifest.json"

def validate() -> list[str]:
    errors: list[str] = []
    if not MANIFEST.is_file():
        return ["site/release-manifest.json is missing"]
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"manifest is invalid JSON: {exc}"]
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), list):
        return ["manifest schema must be 1 and files must be an array"]
    seen: set[str] = set()
    for item in manifest["files"]:
        rel = item.get("path", "")
        if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in pathlib.PurePosixPath(rel).parts:
            errors.append(f"unsafe path: {rel!r}"); continue
        if rel in seen:
            errors.append(f"duplicate path: {rel}"); continue
        seen.add(rel)
        path = SITE / rel
        if not path.is_file():
            errors.append(f"missing release file: {rel}"); continue
        raw = path.read_bytes()
        if item.get("size") != len(raw):
            errors.append(f"size mismatch: {rel}")
        if item.get("sha256") != hashlib.sha256(raw).hexdigest():
            errors.append(f"SHA-256 mismatch: {rel}")
    actual = {p.relative_to(SITE).as_posix() for p in SITE.rglob("*") if p.is_file() and p != MANIFEST}
    if actual != seen:
        errors.append(f"manifest/file set mismatch; unlisted={sorted(actual-seen)}, missing={sorted(seen-actual)}")
    if not (SITE / "index.html").is_file():
        errors.append("site/index.html is required")
    return errors

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    problems = validate()
    if problems:
        print("\n".join(f"ERROR: {x}" for x in problems), file=sys.stderr)
        raise SystemExit(1)
    print("Release manifest valid; site files match declared sizes and SHA-256 digests.")
