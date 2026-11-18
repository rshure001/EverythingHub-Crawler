#!/usr/bin/env python3
"""Append-only explicit feedback and bounded rank-profile updates for eh_quad.

This adapter stores only a document reference, an explicit label, and a
validated numeric feature snapshot. It never stores account identity or raw
user text. Callers own the database transaction and commit/rollback boundary.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.intelligence_core import (  # noqa: E402
    DEFAULT_WEIGHTS,
    FEATURES,
    FEEDBACK_LABELS,
    MAX_FEEDBACK_EVENTS,
    learn_weights,
)

SCHEMA = "eh_quad"
FEEDBACK_VERSION = "explicit_feedback_bounded_v1"
PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_BATCH_SIZE = min(10_000, MAX_FEEDBACK_EVENTS)


def validate_feedback(feedback_label: Any, feature_snapshot: Any,
                      document_id: Any = None) -> dict[str, Any]:
    """Validate a minimal, explicit feedback event before any SQL is executed."""
    if not isinstance(feedback_label, str) or feedback_label not in FEEDBACK_LABELS:
        raise ValueError("feedback_label must be an allowed explicit feedback label")
    if document_id is not None:
        if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
            raise ValueError("document_id must be a positive integer or null")
    if not isinstance(feature_snapshot, Mapping) or set(feature_snapshot) != set(FEATURES):
        raise ValueError("feature_snapshot must contain exactly the four supported numeric features")

    normalized = {}
    for feature in FEATURES:
        value = feature_snapshot[feature]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{feature} must be a finite number between 0 and 1")
        number = float(value)
        if not math.isfinite(number) or number < 0 or number > 1:
            raise ValueError(f"{feature} must be a finite number between 0 and 1")
        normalized[feature] = number
    return {
        "document_id": document_id,
        "feedback_label": feedback_label,
        "feature_snapshot": normalized,
        "feedback_version": FEEDBACK_VERSION,
    }


def validate_profile_name(profile_name: Any) -> str:
    if not isinstance(profile_name, str) or not PROFILE_NAME_PATTERN.fullmatch(profile_name):
        raise ValueError("profile_name must be 1-64 lowercase letters, digits, underscores, or hyphens and start with a letter")
    return profile_name


def validate_batch_size(batch_size: Any) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("batch_size must be an integer")
    value = batch_size
    if value < 1:
        raise ValueError("batch_size must be at least 1")
    return min(value, MAX_BATCH_SIZE)


def _json_object(value: Any, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
    return dict(fallback or {})


def record_feedback(conn, feedback_label: Any, feature_snapshot: Any,
                    document_id: Any = None) -> dict[str, Any]:
    """Insert one validated event. Does not commit; caller controls transaction."""
    event = validate_feedback(feedback_label, feature_snapshot, document_id)
    payload = json.dumps(event["feature_snapshot"], allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into eh_quad.eh_intelligence_feedback
              (document_id, feedback_label, feature_snapshot, feedback_version)
            values (%s, %s, %s::jsonb, %s)
            returning id, created_at
            """,
            (event["document_id"], event["feedback_label"], payload,
             event["feedback_version"]),
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError("feedback insert did not return its generated id")
    return {"id": int(row[0]), "created_at": row[1]}


def refresh_rank_profile(conn, profile_name: str = "default",
                         batch_size: int = MAX_BATCH_SIZE) -> dict[str, Any]:
    """Apply each new feedback row at most once using a locked profile cursor.

    Invalid legacy rows advance the cursor but are ignored by learn_weights.
    Feedback rows are never updated or deleted. The profile update and cursor
    advancement occur in the caller's current transaction.
    """
    name = validate_profile_name(profile_name)
    limit = validate_batch_size(batch_size)
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into eh_quad.eh_rank_profiles (profile_name)
            values (%s)
            on conflict (profile_name) do nothing
            """,
            (name,),
        )
        cur.execute(
            """
            select weights, feedback_count, last_feedback_id
            from eh_quad.eh_rank_profiles
            where profile_name = %s
            for update
            """,
            (name,),
        )
        profile = cur.fetchone()
        if not profile:
            raise RuntimeError("rank profile could not be loaded from eh_quad")

        prior_weights = _json_object(profile[0], DEFAULT_WEIGHTS)
        prior_count = max(0, int(profile[1] or 0))
        last_feedback_id = max(0, int(profile[2] or 0))
        cur.execute(
            """
            select id, feedback_label, feature_snapshot
            from eh_quad.eh_intelligence_feedback
            where id > %s
            order by id asc
            limit %s
            """,
            (last_feedback_id, limit),
        )
        rows = cur.fetchall()

        if not rows:
            return {
                "profile_name": name,
                "feedback_processed": 0,
                "feedback_used": 0,
                "feedback_ignored": 0,
                "feedback_count": prior_count,
                "last_feedback_id": last_feedback_id,
                "weights": prior_weights,
            }

        events = [
            {
                "label": label,
                "feature_snapshot": _json_object(snapshot),
            }
            for _event_id, label, snapshot in rows
        ]
        learned = learn_weights(events, prior=prior_weights)
        cursor_after = max(int(row[0]) for row in rows)
        updated_weights = learned["weights"]
        serialized_weights = json.dumps(updated_weights, allow_nan=False,
                                         sort_keys=True, separators=(",", ":"))
        cur.execute(
            """
            update eh_quad.eh_rank_profiles
            set weights = %s::jsonb,
                feedback_count = feedback_count + %s,
                last_feedback_id = %s,
                updated_at = now()
            where profile_name = %s
            returning feedback_count, last_feedback_id
            """,
            (serialized_weights, learned["feedback_used"], cursor_after, name),
        )
        updated = cur.fetchone()
        if not updated:
            raise RuntimeError("rank profile update did not return its persisted cursor")
    return {
        "profile_name": name,
        "feedback_processed": len(rows),
        "feedback_used": learned["feedback_used"],
        "feedback_ignored": learned["feedback_ignored"],
        "feedback_count": int(updated[0]),
        "last_feedback_id": int(updated[1]),
        "weights": updated_weights,
    }
