#!/usr/bin/env python3
"""Merge crawler JSON state with a newer remote branch without losing work."""
import argparse
import json
from pathlib import Path


def load(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def stamp(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def merge_index(local, remote):
    records = {}
    for source in (remote, local):
        for page in source.get("pages", []):
            url = page.get("url")
            if not url:
                continue
            previous = records.get(url)
            if previous is None or stamp(page.get("fetched_at")) >= stamp(previous.get("fetched_at")):
                records[url] = page
    merged = dict(remote)
    merged.update(local)
    merged["version"] = local.get("version", remote.get("version", 1))
    merged["updated_at"] = max(stamp(local.get("updated_at")), stamp(remote.get("updated_at")))
    merged["pages"] = list(records.values())
    return merged


def merge_frontier(local, remote):
    seen = set(remote.get("seen", [])) | set(local.get("seen", []))
    queue = {}
    # Keep remote order first, then append newly discovered local entries.
    for source in (remote, local):
        for item in source.get("queue", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            url = item["url"]
            depth = int(item.get("depth", 0))
            if url not in queue or depth < int(queue[url].get("depth", 0)):
                queue[url] = {"url": url, "depth": depth}
    return {
        "updated_at": max(stamp(local.get("updated_at")), stamp(remote.get("updated_at"))),
        "queue": list(queue.values()),
        "seen": sorted(seen),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-index", required=True)
    parser.add_argument("--remote-index", required=True)
    parser.add_argument("--local-frontier", required=True)
    parser.add_argument("--remote-frontier", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    local_index = load(args.local_index, {"version": 1, "pages": []})
    remote_index = load(args.remote_index, {"version": 1, "pages": []})
    local_frontier = load(args.local_frontier, {"queue": [], "seen": []})
    remote_frontier = load(args.remote_frontier, {"queue": [], "seen": []})

    merged_index = merge_index(local_index, remote_index)
    merged_frontier = merge_frontier(local_frontier, remote_frontier)
    merged_index["queue_remaining"] = len(merged_frontier["queue"])
    merged_index["urls_discovered_total"] = len(merged_frontier["seen"])

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.json").write_text(
        json.dumps(merged_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "frontier.json").write_text(
        json.dumps(merged_frontier, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Merged {len(merged_index['pages'])} unique page records; "
        f"{len(merged_frontier['queue'])} queued URLs; "
        f"{len(merged_frontier['seen'])} discovered URLs."
    )


if __name__ == "__main__":
    main()
