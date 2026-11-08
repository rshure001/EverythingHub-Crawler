"""Testable primitives for an eight-lane, polite crawler fleet.

This module is deliberately storage-agnostic. Production adapters can persist the
same queue/lease transitions in a durable database without changing the contract.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, Iterable, Optional
from urllib.parse import urlsplit

LANES = ("general", "science", "news", "technology", "business",
         "government", "culture", "nature")


class State(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY = "retry"
    DONE = "done"
    DEAD = "dead"


@dataclass
class Job:
    url: str
    lane: str
    depth: int = 0
    attempts: int = 0
    state: State = State.QUEUED
    lease_owner: Optional[str] = None
    lease_until: float = 0.0
    next_attempt_at: float = 0.0
    last_error: Optional[str] = None


def lane_for(url: str) -> str:
    """Stable topic routing; callers may override with classifier output."""
    host = urlsplit(url).hostname or ""
    path = urlsplit(url).path.lower()
    text = host + " " + path
    rules = (
        ("science", ("nasa", "nih", "science", "research", "nature.com")),
        ("news", ("news", "reuters", "bbc", "apnews", "npr")),
        ("technology", ("github", "software", "developer", "tech", "ietf")),
        ("business", ("business", "finance", "market", "company", "sec.gov")),
        ("government", (".gov", "congress", "senate", "supreme-court")),
        ("culture", ("museum", "history", "arts", "education", "library")),
        ("nature", ("wildlife", "environment", "noaa", "usgs", "nps.gov")),
    )
    for lane, tokens in rules:
        if any(token in text for token in tokens):
            return lane
    return "general"


@dataclass
class FleetQueue:
    max_attempts: int = 5
    base_backoff: float = 2.0
    max_backoff: float = 300.0
    lanes: Dict[str, Deque[str]] = field(
        default_factory=lambda: {name: deque() for name in LANES})
    jobs: Dict[str, Job] = field(default_factory=dict)
    dead_letters: Deque[str] = field(default_factory=deque)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def enqueue(self, url: str, lane: Optional[str] = None, depth: int = 0) -> bool:
        with self._lock:
            if url in self.jobs:
                return False
            chosen = lane or lane_for(url)
            if chosen not in self.lanes:
                raise ValueError(f"unknown crawl lane: {chosen}")
            self.jobs[url] = Job(url=url, lane=chosen, depth=depth)
            self.lanes[chosen].append(url)
            return True

    def claim(self, lane: str, worker: str, lease_seconds: float = 60,
              now: Optional[float] = None) -> Optional[Job]:
        now = time.time() if now is None else now
        with self._lock:
            if lane not in self.lanes:
                raise ValueError(f"unknown crawl lane: {lane}")
            q = self.lanes[lane]
            for _ in range(len(q)):
                url = q.popleft()
                job = self.jobs[url]
                if job.state == State.LEASED and job.lease_until <= now:
                    job.state = State.RETRY
                    job.lease_owner = None
                    job.next_attempt_at = now
                if job.state in (State.QUEUED, State.RETRY) and job.next_attempt_at <= now:
                    job.state = State.LEASED
                    job.lease_owner = worker
                    job.lease_until = now + lease_seconds
                    job.attempts += 1
                    return job
                q.append(url)
            return None

    def complete(self, url: str, worker: str) -> None:
        with self._lock:
            job = self._owned_lease(url, worker)
            job.state = State.DONE
            job.lease_owner = None
            job.lease_until = 0

    def fail(self, url: str, worker: str, error: str,
             now: Optional[float] = None) -> State:
        now = time.time() if now is None else now
        with self._lock:
            job = self._owned_lease(url, worker)
            job.last_error = error[:500]
            job.lease_owner = None
            job.lease_until = 0
            if job.attempts >= self.max_attempts:
                job.state = State.DEAD
                self.dead_letters.append(url)
            else:
                job.state = State.RETRY
                delay = min(self.max_backoff, self.base_backoff * (2 ** (job.attempts - 1)))
                job.next_attempt_at = now + delay
                self.lanes[job.lane].append(url)
            return job.state

    def _owned_lease(self, url: str, worker: str) -> Job:
        job = self.jobs[url]
        if job.state != State.LEASED or job.lease_owner != worker:
            raise RuntimeError("worker does not own an active lease")
        return job

    def recover_expired(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        recovered = 0
        with self._lock:
            for job in self.jobs.values():
                if job.state == State.LEASED and job.lease_until <= now:
                    job.state = State.RETRY
                    job.lease_owner = None
                    job.lease_until = now
                    self.lanes[job.lane].append(job.url)
                    recovered += 1
        return recovered

    def counts(self) -> dict:
        with self._lock:
            result = {state.value: 0 for state in State}
            for job in self.jobs.values():
                result[job.state.value] += 1
            result["queued_by_lane"] = {lane: len(q) for lane, q in self.lanes.items()}
            return result


class HostRateLimiter:
    """One shared limiter across all lanes/workers for each origin."""
    def __init__(self, min_interval: float = 1.0):
        if min_interval < 0:
            raise ValueError("min_interval cannot be negative")
        self.min_interval = min_interval
        self._next: Dict[str, float] = {}
        self._lock = threading.Lock()

    def reserve(self, url: str, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        parts = urlsplit(url)
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        with self._lock:
            allowed_at = max(now, self._next.get(origin, now))
            self._next[origin] = allowed_at + self.min_interval
            return allowed_at


class AcceptedIndex:
    """Counts only first-time, validated records accepted by the index boundary."""
    def __init__(self):
        self._records: Dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(canonical_url: str) -> str:
        return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()

    def accept(self, canonical_url: str, title: str, text: str) -> bool:
        if not canonical_url.startswith(("http://", "https://")):
            return False
        if not title.strip() or len(text.strip()) < 80:
            return False
        key = self.key(canonical_url)
        with self._lock:
            if key in self._records:
                return False
            self._records[key] = canonical_url
            return True

    def count(self) -> int:
        with self._lock:
            return len(self._records)
