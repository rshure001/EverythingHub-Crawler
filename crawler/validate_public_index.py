#!/usr/bin/env python3
"""Validate the generated public search shards against the canonical crawler index."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from lane_classifier import VALID_LANES

ROOT = Path(__file__).resolve().parents[1]
MAX_SHARD_BYTES = 350_000


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    source_path = root / "data" / "index.json"
    search_dir = root / "public-search"
    manifest_path = search_dir / "manifest.json"

    if not source_path.is_file():
        return ["data/index.json is missing"]
    if not manifest_path.is_file():
        return ["public-search/manifest.json is missing"]

    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"index or manifest is invalid JSON: {exc}"]

    if not isinstance(source.get("pages"), list):
        errors.append("data/index.json pages must be an array")
        source_pages = []
    else:
        source_pages = source["pages"]

    if manifest.get("version") != 1:
        errors.append("public search manifest version must be 1")
    integrity = manifest.get("integrity")
    if integrity not in (None, "sha256"):
        errors.append(f"unsupported public search integrity mode: {integrity!r}")
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        return errors + ["manifest shards must be an array"]
    if manifest.get("shard_count") != len(shards):
        errors.append("manifest shard_count does not match shards array")

    expected_urls = {
        page.get("url")
        for page in source_pages
        if isinstance(page, dict)
        and isinstance(page.get("url"), str)
        and page["url"].startswith(("http://", "https://"))
    }

    seen_urls: set[str] = set()
    declared_files: set[str] = set()
    total_pages = 0

    for entry in shards:
        if not isinstance(entry, dict):
            errors.append("manifest contains a non-object shard entry")
            continue
        name = entry.get("file")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.startswith("shard-")
            or not name.endswith(".json")
        ):
            errors.append(f"unsafe shard filename: {name!r}")
            continue
        if name in declared_files:
            errors.append(f"duplicate shard filename: {name}")
            continue
        declared_files.add(name)
        path = search_dir / name
        if not path.is_file():
            errors.append(f"missing shard file: {name}")
            continue

        raw = path.read_bytes()
        if len(raw) > MAX_SHARD_BYTES:
            errors.append(f"shard exceeds {MAX_SHARD_BYTES} bytes: {name}")
        if entry.get("bytes") != len(raw):
            errors.append(f"byte-size mismatch: {name}")

        digest = entry.get("sha256")
        if digest is None and integrity == "sha256":
            errors.append(f"missing SHA-256 digest: {name}")
        elif digest is not None:
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                errors.append(f"invalid SHA-256 digest: {name}")
            elif hashlib.sha256(raw).hexdigest() != digest:
                errors.append(f"SHA-256 mismatch: {name}")

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            errors.append(f"invalid JSON in {name}: {exc}")
            continue
        if not isinstance(payload, dict) or payload.get("version") != 1:
            errors.append(f"invalid shard schema: {name}")
            continue
        pages = payload.get("pages")
        if not isinstance(pages, list):
            errors.append(f"shard pages must be an array: {name}")
            continue
        if entry.get("pages") != len(pages):
            errors.append(f"page-count mismatch: {name}")

        total_pages += len(pages)
        for page in pages:
            if not isinstance(page, dict):
                errors.append(f"non-object page record in {name}")
                continue
            url = page.get("url")
            if not isinstance(url, str):
                errors.append(f"missing URL in {name}")
                continue
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                errors.append(f"non-public-web URL in {name}: {url[:160]}")
                continue
            lane = page.get("lane")
            if not isinstance(lane, str) or lane not in VALID_LANES:
                errors.append(f"missing or invalid search lane in {name}: {lane!r}")
            if url in seen_urls:
                errors.append(f"duplicate URL across public shards: {url[:160]}")
            seen_urls.add(url)

    if manifest.get("total_pages") != total_pages:
        errors.append("manifest total_pages does not match actual shard records")
    if total_pages != len(seen_urls):
        errors.append("public shard record count differs from unique URL count")
    if seen_urls != expected_urls:
        errors.append(
            f"public shards do not match canonical index; "
            f"missing={len(expected_urls - seen_urls)}, extra={len(seen_urls - expected_urls)}"
        )

    actual_files = {path.name for path in search_dir.glob("shard-*.json")}
    if actual_files != declared_files:
        errors.append(
            f"shard file set mismatch; unlisted={sorted(actual_files - declared_files)}, "
            f"missing={sorted(declared_files - actual_files)}"
        )
    return errors


def main() -> None:
    errors = validate()
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        raise SystemExit(1)
    manifest = json.loads((ROOT / "public-search" / "manifest.json").read_text(encoding="utf-8"))
    print(
        "Public search index valid: "
        f"{manifest['total_pages']:,} pages across {manifest['shard_count']:,} shards; "
        "all shard sizes, SHA-256 digests, URLs, and records match data/index.json."
    )


if __name__ == "__main__":
    main()
