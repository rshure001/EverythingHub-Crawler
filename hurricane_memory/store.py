"""Hurricane Memory: a small, file-backed append-only memory journal.

This prototype uses only the Python standard library. It is intentionally
separate from Everything Hub's production database and crawler state.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class MemoryIntegrityError(ValueError):
    """Raised when the journal contains malformed or altered data."""


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class MemoryStore:
    """Append-only JSONL store with idempotent writes and checksum validation."""

    FORMAT_VERSION = 1

    def __init__(self, journal_path: str | os.PathLike[str]):
        self.path = Path(journal_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _read_all(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    raise MemoryIntegrityError(
                        f"Incomplete journal record at line {line_number}"
                    )
                try:
                    envelope = json.loads(line)
                    payload = envelope["payload"]
                    checksum = envelope["sha256"]
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise MemoryIntegrityError(
                        f"Malformed journal record at line {line_number}"
                    ) from exc
                if not isinstance(payload, dict) or checksum != _digest(payload):
                    raise MemoryIntegrityError(
                        f"Checksum mismatch at line {line_number}"
                    )
                if payload.get("format_version") != self.FORMAT_VERSION:
                    raise MemoryIntegrityError(
                        f"Unsupported format version at line {line_number}"
                    )
                entries.append(payload)
        return entries

    def append(
        self,
        text: str,
        *,
        kind: str = "note",
        tags: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one memory. Repeating memory_id returns the original entry."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be a non-empty string")
        clean_tags = sorted({str(tag).strip() for tag in tags if str(tag).strip()})
        safe_metadata = metadata or {}
        if not isinstance(safe_metadata, dict):
            raise ValueError("metadata must be a dictionary")
        with self._lock:
            entries = self._read_all()
            if memory_id is not None:
                for entry in entries:
                    if entry["id"] == memory_id:
                        return entry
            payload = {
                "format_version": self.FORMAT_VERSION,
                "id": memory_id or str(uuid.uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "kind": kind.strip(),
                "text": text.strip(),
                "tags": clean_tags,
                "metadata": safe_metadata,
            }
            envelope = {"payload": payload, "sha256": _digest(payload)}
            encoded = (_canonical(envelope).decode("utf-8") + "\n").encode("utf-8")
            fd = os.open(self.path, os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return payload

    def get(self, memory_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((entry for entry in self._read_all()
                         if entry["id"] == memory_id), None)

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Case-insensitive substring search across text, kind, and tags."""
        if limit < 1:
            raise ValueError("limit must be positive")
        needle = query.casefold().strip()
        if not needle:
            return []
        with self._lock:
            matches = []
            for entry in reversed(self._read_all()):
                haystack = " ".join(
                    [entry["text"], entry["kind"], *entry["tags"]]
                ).casefold()
                if needle in haystack:
                    matches.append(entry)
                    if len(matches) >= limit:
                        break
            return matches

    def verify(self) -> dict[str, Any]:
        """Validate every record and report journal size and last entry."""
        with self._lock:
            entries = self._read_all()
            return {
                "valid": True,
                "records": len(entries),
                "last_id": entries[-1]["id"] if entries else None,
                "journal_bytes": self.path.stat().st_size,
            }
