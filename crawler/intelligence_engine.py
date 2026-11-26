#!/usr/bin/env python3
"""Durable, lease-based task queue for the Everything Hub Intelligence Engine.

This module is an orchestration primitive, not an autonomous code publisher.
Workers claim only tasks matching their capability; all state transitions are
transactional and append-only events preserve an audit trail.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/intelligence-engine/queue.sqlite3")
LANES = {"research", "catalog", "crawler", "search", "quality", "release", "ops"}
STATUSES = {"queued", "leased", "succeeded", "failed", "blocked"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or now_utc()).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS tasks (
      task_id TEXT PRIMARY KEY,
      idempotency_key TEXT UNIQUE,
      title TEXT NOT NULL,
      lane TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued',
      priority INTEGER NOT NULL DEFAULT 50,
      attempts INTEGER NOT NULL DEFAULT 0,
      max_attempts INTEGER NOT NULL DEFAULT 3,
      available_at TEXT NOT NULL,
      lease_owner TEXT,
      lease_token TEXT,
      lease_expires_at TEXT,
      result_json TEXT,
      last_error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_claim
      ON tasks(status, lane, priority DESC, available_at, created_at);
    CREATE TABLE IF NOT EXISTS events (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL REFERENCES tasks(task_id),
      event_type TEXT NOT NULL,
      actor TEXT NOT NULL,
      details_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, event_id);
    """)


def event(db: sqlite3.Connection, task_id: str, kind: str,
          actor: str, details: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO events(task_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)",
        (task_id, kind, actor, json.dumps(details, sort_keys=True), stamp()),
    )


def enqueue(db: sqlite3.Connection, *, title: str, lane: str,
            payload: dict[str, Any] | None = None, priority: int = 50,
            max_attempts: int = 3, idempotency_key: str | None = None,
            actor: str = "operator") -> str:
    if lane not in LANES:
        raise ValueError(f"unsupported lane: {lane}")
    if not title.strip():
        raise ValueError("title is required")
    if not 1 <= max_attempts <= 20:
        raise ValueError("max_attempts must be between 1 and 20")
    task_id = str(uuid.uuid4())
    created = stamp()
    db.execute("BEGIN IMMEDIATE")
    try:
        if idempotency_key:
            row = db.execute(
                "SELECT task_id FROM tasks WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row:
                db.execute("COMMIT")
                return str(row["task_id"])
        db.execute(
            """INSERT INTO tasks(task_id,idempotency_key,title,lane,payload_json,
               priority,max_attempts,available_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (task_id, idempotency_key, title.strip(), lane,
             json.dumps(payload or {}, sort_keys=True), int(priority),
             max_attempts, created, created, created),
        )
        event(db, task_id, "queued", actor, {"lane": lane, "priority": priority})
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return task_id


def claim(db: sqlite3.Connection, *, lane: str, worker: str,
          lease_seconds: int = 900) -> dict[str, Any] | None:
    if lane not in LANES:
        raise ValueError(f"unsupported lane: {lane}")
    if not worker.strip():
        raise ValueError("worker identity is required")
    if not 30 <= lease_seconds <= 86400:
        raise ValueError("lease_seconds must be between 30 and 86400")
    now = now_utc()
    token = str(uuid.uuid4())
    expires = stamp(now + timedelta(seconds=lease_seconds))
    db.execute("BEGIN IMMEDIATE")
    try:
        # Expired leases are returned to the queue, or terminally failed when
        # their retry budget is exhausted. This is safe under concurrent workers.
        expired = db.execute(
            "SELECT task_id,attempts,max_attempts FROM tasks "
            "WHERE status='leased' AND lease_expires_at<=?",
            (stamp(now),),
        ).fetchall()
        for row in expired:
            next_status = "queued" if row["attempts"] < row["max_attempts"] else "failed"
            db.execute(
                """UPDATE tasks SET status=?,lease_owner=NULL,lease_token=NULL,
                   lease_expires_at=NULL,updated_at=? WHERE task_id=?""",
                (next_status, stamp(now), row["task_id"]),
            )
            event(db, row["task_id"], "lease_expired", worker,
                  {"next_status": next_status})
        row = db.execute(
            """SELECT * FROM tasks WHERE status='queued' AND lane=?
               AND available_at<=? AND attempts<max_attempts
               ORDER BY priority DESC,created_at,task_id LIMIT 1""",
            (lane, stamp(now)),
        ).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        db.execute(
            """UPDATE tasks SET status='leased',attempts=attempts+1,
               lease_owner=?,lease_token=?,lease_expires_at=?,updated_at=?
               WHERE task_id=? AND status='queued'""",
            (worker, token, expires, stamp(now), row["task_id"]),
        )
        event(db, row["task_id"], "leased", worker,
              {"lease_expires_at": expires, "attempt": row["attempts"] + 1})
        claimed = dict(row)
        claimed.update(status="leased", attempts=row["attempts"] + 1,
                       lease_owner=worker, lease_token=token,
                       lease_expires_at=expires)
        claimed["payload"] = json.loads(claimed.pop("payload_json"))
        db.execute("COMMIT")
        return claimed
    except Exception:
        db.execute("ROLLBACK")
        raise


def finish(db: sqlite3.Connection, *, task_id: str, lease_token: str,
           actor: str, result: dict[str, Any] | None = None) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT status,lease_token FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row or row["status"] != "leased" or row["lease_token"] != lease_token:
            raise ValueError("task lease is missing, expired, or owned by another worker")
        db.execute(
            """UPDATE tasks SET status='succeeded',result_json=?,lease_owner=NULL,
               lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=?""",
            (json.dumps(result or {}, sort_keys=True), stamp(), task_id),
        )
        event(db, task_id, "succeeded", actor, result or {})
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def fail(db: sqlite3.Connection, *, task_id: str, lease_token: str,
         actor: str, error: str, retry_delay_seconds: int = 60) -> str:
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT status,lease_token,attempts,max_attempts FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not row or row["status"] != "leased" or row["lease_token"] != lease_token:
            raise ValueError("task lease is missing, expired, or owned by another worker")
        status = "queued" if row["attempts"] < row["max_attempts"] else "failed"
        available = stamp(now_utc() + timedelta(seconds=max(0, retry_delay_seconds)))
        db.execute(
            """UPDATE tasks SET status=?,available_at=?,last_error=?,lease_owner=NULL,
               lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=?""",
            (status, available, error[:4000], stamp(), task_id),
        )
        event(db, task_id, "failed_attempt", actor,
              {"status": status, "error": error[:1000]})
        db.execute("COMMIT")
        return status
    except Exception:
        db.execute("ROLLBACK")
        raise


def summary(db: sqlite3.Connection) -> dict[str, Any]:
    counts = {row["status"]: row["n"] for row in db.execute(
        "SELECT status,COUNT(*) AS n FROM tasks GROUP BY status"
    )}
    lanes = [dict(row) for row in db.execute(
        "SELECT lane,status,COUNT(*) AS count FROM tasks GROUP BY lane,status ORDER BY lane,status"
    )]
    return {"counts": counts, "lanes": lanes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    add = sub.add_parser("enqueue")
    add.add_argument("--title", required=True)
    add.add_argument("--lane", choices=sorted(LANES), required=True)
    add.add_argument("--payload", default="{}")
    add.add_argument("--priority", type=int, default=50)
    add.add_argument("--idempotency-key")
    take = sub.add_parser("claim")
    take.add_argument("--lane", choices=sorted(LANES), required=True)
    take.add_argument("--worker", required=True)
    take.add_argument("--lease-seconds", type=int, default=900)
    sub.add_parser("summary")
    args = parser.parse_args(argv)
    db = connect(args.db)
    initialize(db)
    if args.command == "init":
        print(json.dumps({"initialized": str(args.db)}))
    elif args.command == "enqueue":
        task_id = enqueue(db, title=args.title, lane=args.lane,
                          payload=json.loads(args.payload), priority=args.priority,
                          idempotency_key=args.idempotency_key)
        print(json.dumps({"task_id": task_id}))
    elif args.command == "claim":
        print(json.dumps(claim(db, lane=args.lane, worker=args.worker,
                               lease_seconds=args.lease_seconds)))
    else:
        print(json.dumps(summary(db), indent=2))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
