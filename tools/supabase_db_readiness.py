#!/usr/bin/env python3
"""Safe, read-only readiness gate for the managed Everything Hub Postgres database.

This tool does not restart, restore, migrate, or mutate the database. It waits
for Supabase/Postgres to accept a trivial read-only query, classifies failures,
and exits non-zero while the database is unavailable. This prevents downstream
schema/import jobs from running against a database in hot-standby recovery.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable

RETRYABLE_SQLSTATES = {
    "57P03",  # cannot_connect_now / database starting or in recovery
    "53300",  # too_many_connections
    "08000", "08001", "08003", "08004", "08006", "08007", "08P01",
}
AUTH_SQLSTATES = {"28P01", "28000"}


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)


def _category(exc: BaseException) -> tuple[str, bool]:
    state = _sqlstate(exc)
    if state in AUTH_SQLSTATES:
        return "authentication_failed", False
    if state == "57P03":
        return "postgres_not_accepting_connections", True
    if state == "53300":
        return "connection_capacity_exhausted", True
    if state and (state.startswith("08") or state == "08P01"):
        return "connection_transport_error", True
    message = str(exc).lower()
    if "hot standby mode is disabled" in message or "57p03" in message:
        return "postgres_not_accepting_connections", True
    if any(term in message for term in ("timed out", "timeout", "connection refused", "could not connect")):
        return "connection_transport_error", True
    return "database_probe_failed", False


def probe_until_ready(
    dsn: str,
    connect: Callable,
    attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """Run SELECT 1 with bounded exponential backoff; never issue writes."""
    if not dsn or not dsn.strip():
        raise ValueError("A database connection string is required.")
    if attempts < 1 or attempts > 10:
        raise ValueError("attempts must be between 1 and 10")
    if base_delay < 0 or max_delay < 0:
        raise ValueError("delays cannot be negative")

    last_category = "unknown"
    last_state = None
    for attempt in range(1, attempts + 1):
        conn = None
        try:
            conn = connect(
                dsn,
                connect_timeout=5,
                options="-c default_transaction_read_only=on",
            )
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
            if not row or row[0] != 1:
                raise RuntimeError("Readiness query returned an unexpected result.")
            return {
                "ready": True,
                "attempts": attempt,
                "category": "ready",
                "sqlstate": None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "probe": "SELECT 1 (read-only)",
            }
        except Exception as exc:  # driver errors are intentionally normalized
            last_category, retryable = _category(exc)
            last_state = _sqlstate(exc)
            if not retryable or attempt == attempts:
                break
            sleeper(min(base_delay * (2 ** (attempt - 1)), max_delay))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    return {
        "ready": False,
        "attempts": attempt,
        "category": last_category,
        "sqlstate": last_state,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "probe": "SELECT 1 (read-only)",
        "next_action": (
            "Stop schema/import jobs. Wait for Supabase Postgres recovery and rerun this guard; "
            "do not pause, restore, reset, or migrate the project while it rejects connections."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--base-delay", type=float, default=2.0)
    parser.add_argument("--max-delay", type=float, default=15.0)
    args = parser.parse_args(argv)
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print(json.dumps({"ready": False, "category": "missing_configuration",
                          "error": "Set SUPABASE_DB_URL in the environment."}, indent=2))
        return 3
    try:
        import psycopg
    except ImportError:
        print(json.dumps({"ready": False, "category": "missing_dependency",
                          "error": "Install the pinned psycopg driver: psycopg[binary]."}, indent=2))
        return 3

    result = probe_until_ready(
        dsn, psycopg.connect, attempts=args.attempts,
        base_delay=args.base_delay, max_delay=args.max_delay,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
