#!/usr/bin/env python3
"""Build a bounded ranking-policy candidate from explicit, privacy-safe feedback.

Input is JSONL. Each record must contain only:
  {"lane":"web","label":1}
where label is an explicit user relevance judgement: 1 useful, 0 not useful.
Do not store raw queries, URLs, account IDs, IPs, device IDs, or free-form text here.
This tool produces a reviewable candidate; it never changes production ranking.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

MIN_LABELS_PER_LANE = 20
MIN_LANES = 2
MIN_MULTIPLIER = 0.90
MAX_MULTIPLIER = 1.10
PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0


def load_feedback(path: Path) -> dict[str, list[int]]:
    by_lane: dict[str, list[int]] = defaultdict(list)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item: Any = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"line {line_number} must be an object")
            lane = item.get("lane")
            label = item.get("label")
            if not isinstance(lane, str) or not lane.strip() or len(lane) > 40:
                raise ValueError(f"line {line_number} has an invalid lane")
            if isinstance(label, bool) or label not in (0, 1):
                raise ValueError(f"line {line_number} label must be 0 or 1")
            by_lane[lane.strip().casefold()].append(label)
    return dict(by_lane)


def build_candidate(by_lane: dict[str, list[int]]) -> dict[str, Any]:
    eligible = {
        lane: labels for lane, labels in by_lane.items()
        if len(labels) >= MIN_LABELS_PER_LANE
    }
    totals = [label for labels in eligible.values() for label in labels]
    if len(eligible) < MIN_LANES or not totals:
        return {
            "schema_version": 1,
            "status": "insufficient_feedback",
            "minimum_labels_per_lane": MIN_LABELS_PER_LANE,
            "minimum_lanes": MIN_LANES,
            "lane_weights": {},
            "sample_counts": {lane: len(labels) for lane, labels in sorted(by_lane.items())},
        }

    overall_rate = (sum(totals) + PRIOR_ALPHA) / (len(totals) + PRIOR_ALPHA + PRIOR_BETA)
    weights = {}
    counts = {}
    for lane, labels in sorted(eligible.items()):
        posterior_rate = (sum(labels) + PRIOR_ALPHA) / (len(labels) + PRIOR_ALPHA + PRIOR_BETA)
        raw = posterior_rate / overall_rate
        weights[lane] = round(max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, raw)), 4)
        counts[lane] = len(labels)
    return {
        "schema_version": 1,
        "status": "candidate_requires_review",
        "method": "beta-smoothed-explicit-feedback-lane-rate",
        "overall_positive_rate": round(overall_rate, 4),
        "lane_weights": weights,
        "sample_counts": counts,
        "bounds": {"minimum": MIN_MULTIPLIER, "maximum": MAX_MULTIPLIER},
        "production_applied": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/feedback.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/learned-policy-candidate.json"))
    args = parser.parse_args()
    candidate = build_candidate(load_feedback(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": candidate["status"],
        "lanes": len(candidate["lane_weights"]),
        "output": str(args.output),
        "production_applied": False,
    }, indent=2))


if __name__ == "__main__":
    main()
