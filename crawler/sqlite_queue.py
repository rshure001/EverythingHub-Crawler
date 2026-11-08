"""Durable SQLite implementation of the eight-lane crawler queue.

SQLite is suitable for a single-host worker pool and development validation.
A multi-host production fleet will need a shared transactional database adapter.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from crawler.fleet import LANES, Job, State, lane_for


class SQLiteFleetQueue:
    def __init__(self, path: str, max_attempts: int = 5,
                 base_backoff: float = 2.0, max_backoff: float = 300.0):
        self.path = str(path)
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS fleet_jobs (
                    url TEXT PRIMARY KEY,
                    lane TEXT NOT NULL,
                    depth INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until REAL NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS fleet_jobs_claim_idx
                ON fleet_jobs(lane, state, next_attempt_at, created_at)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS fleet_dead_letters (
                    url TEXT PRIMARY KEY,
                    lane TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    last_error TEXT,
                    failed_at REAL NOT NULL
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    def enqueue(self, url: str, lane: Optional[str] = None, depth: int = 0) -> bool:
        chosen = lane or lane_for(url)
        if chosen not in LANES:
            raise ValueError(f"unknown crawl lane: {chosen}")
        now = time.time()
        with self._connect() as db:
            cur = db.execute("""
                INSERT OR IGNORE INTO fleet_jobs
                (url,lane,depth,state,created_at,updated_at)
                VALUES (?,?,?,'queued',?,?)
            """, (url, chosen, depth, now, now))
            return cur.rowcount == 1

    def claim(self, lane: str, worker: str, lease_seconds: float = 60,
              now: Optional[float] = None) -> Optional[Job]:
        if lane not in LANES:
            raise ValueError(f"unknown crawl lane: {lane}")
        now = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                UPDATE fleet_jobs SET state='retry', lease_owner=NULL,
                    lease_until=0, next_attempt_at=?, updated_at=?
                WHERE lane=? AND state='leased' AND lease_until<=?
            """, (now, now, lane, now))
            row = db.execute("""
                SELECT * FROM fleet_jobs
                WHERE lane=? AND state IN ('queued','retry') AND next_attempt_at<=?
                ORDER BY created_at, url LIMIT 1
            """, (lane, now)).fetchone()
            if row is None:
                db.commit()
                return None
            changed = db.execute("""
                UPDATE fleet_jobs SET state='leased', lease_owner=?,
                    lease_until=?, attempts=attempts+1, updated_at=?
                WHERE url=? AND state IN ('queued','retry')
            """, (worker, now + lease_seconds, now, row["url"])).rowcount
            if changed != 1:
                db.rollback()
                return None
            db.commit()
            return Job(
                url=row["url"], lane=row["lane"], depth=row["depth"],
                attempts=row["attempts"] + 1, state=State.LEASED,
                lease_owner=worker, lease_until=now + lease_seconds,
                next_attempt_at=row["next_attempt_at"], last_error=row["last_error"]
            )

    def complete(self, url: str, worker: str) -> None:
        with self._connect() as db:
            cur = db.execute("""
                UPDATE fleet_jobs SET state='done', lease_owner=NULL,
                    lease_until=0, updated_at=?
                WHERE url=? AND state='leased' AND lease_owner=?
            """, (time.time(), url, worker))
            if cur.rowcount != 1:
                raise RuntimeError("worker does not own an active lease")

    def fail(self, url: str, worker: str, error: str,
             now: Optional[float] = None) -> State:
        now = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT * FROM fleet_jobs
                WHERE url=? AND state='leased' AND lease_owner=?
            """, (url, worker)).fetchone()
            if row is None:
                db.rollback()
                raise RuntimeError("worker does not own an active lease")
            attempts = row["attempts"]
            message = error[:500]
            if attempts >= self.max_attempts:
                state = State.DEAD
                db.execute("""
                    INSERT OR REPLACE INTO fleet_dead_letters
                    (url,lane,attempts,last_error,failed_at) VALUES (?,?,?,?,?)
                """, (url, row["lane"], attempts, message, now))
                db.execute("""
                    UPDATE fleet_jobs SET state='dead', lease_owner=NULL,
                        lease_until=0, last_error=?, updated_at=? WHERE url=?
                """, (message, now, url))
            else:
                state = State.RETRY
                delay = min(self.max_backoff, self.base_backoff * (2 ** (attempts - 1)))
                db.execute("""
                    UPDATE fleet_jobs SET state='retry', lease_owner=NULL,
                        lease_until=0, next_attempt_at=?, last_error=?, updated_at=?
                    WHERE url=?
                """, (now + delay, message, now, url))
            db.commit()
            return state

    def recover_expired(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        with self._connect() as db:
            cur = db.execute("""
                UPDATE fleet_jobs SET state='retry', lease_owner=NULL,
                    lease_until=0, next_attempt_at=?, updated_at=?
                WHERE state='leased' AND lease_until<=?
            """, (now, now, now))
            return cur.rowcount

    def counts(self) -> dict:
        with self._connect() as db:
            rows = db.execute(
                "SELECT state, COUNT(*) AS n FROM fleet_jobs GROUP BY state"
            ).fetchall()
            counts = {state.value: 0 for state in State}
            counts.update({row["state"]: row["n"] for row in rows})
            counts["queued_by_lane"] = {lane: 0 for lane in LANES}
            for row in db.execute("""
                SELECT lane, COUNT(*) AS n FROM fleet_jobs
                WHERE state IN ('queued','retry') GROUP BY lane
            """):
                counts["queued_by_lane"][row["lane"]] = row["n"]
            counts["dead_letters"] = db.execute(
                "SELECT COUNT(*) FROM fleet_dead_letters"
            ).fetchone()[0]
            return counts

    def get(self, url: str) -> Optional[Job]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM fleet_jobs WHERE url=?", (url,)).fetchone()
        if row is None:
            return None
        return Job(
            url=row["url"], lane=row["lane"], depth=row["depth"],
            attempts=row["attempts"], state=State(row["state"]),
            lease_owner=row["lease_owner"], lease_until=row["lease_until"],
            next_attempt_at=row["next_attempt_at"], last_error=row["last_error"]
        )
