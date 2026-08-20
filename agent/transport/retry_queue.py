"""Persistent, offline-tolerant retry queue backed by SQLite (AGENT_SPEC.md §4.3).

When the backend is unreachable (network down, timeout, or 5xx), payloads are
buffered here and retried oldest-first on later cycles. Because the store is
on-disk, buffered payloads survive an agent restart or a server reboot.
Payloads older than the retention window (default 24h) are dropped rather than
sent — stale health data is not worth delivering.

Auth failures (401/403) are deliberately *not* handled here: the sender drops
those payloads immediately (a bad token won't fix itself by retrying), so they
never enter this queue.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.logging_setup import get_logger

_log = get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_created_at ON queue (created_at);
"""


@dataclass(frozen=True)
class QueuedItem:
    """One buffered payload awaiting redelivery."""

    id: int
    payload: dict[str, Any]
    created_at: float


class RetryQueue:
    """A small FIFO durable queue with a time-based retention window."""

    def __init__(
        self,
        path: str,
        *,
        retention_hours: float = 24.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._retention_seconds = retention_hours * 3600.0
        self._clock = clock

        db_path = Path(path)
        if db_path.parent and not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)

        # Single-threaded agent loop, so the default connection is fine. WAL
        # improves durability/consistency across abrupt restarts.
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- writes -------------------------------------------------------- #
    def enqueue(self, payload: dict[str, Any], *, created_at: float | None = None) -> int:
        """Buffer a payload; returns its row id."""
        ts = created_at if created_at is not None else self._clock()
        cur = self._conn.execute(
            "INSERT INTO queue (payload, created_at) VALUES (?, ?)",
            (json.dumps(payload, separators=(",", ":")), ts),
        )
        self._conn.commit()
        _log.debug("buffered payload id=%s (queue depth %d)", cur.lastrowid, self.count())
        return int(cur.lastrowid)

    def delete(self, item_id: int) -> None:
        """Remove a delivered payload."""
        self._conn.execute("DELETE FROM queue WHERE id = ?", (item_id,))
        self._conn.commit()

    def purge_expired(self) -> int:
        """Drop payloads older than the retention window; returns count dropped."""
        cutoff = self._clock() - self._retention_seconds
        cur = self._conn.execute("DELETE FROM queue WHERE created_at < ?", (cutoff,))
        self._conn.commit()
        dropped = cur.rowcount or 0
        if dropped:
            _log.warning("dropped %d payload(s) past %.0fh retention", dropped, self._retention_seconds / 3600)
        return dropped

    # ---- reads --------------------------------------------------------- #
    def peek(self, limit: int = 1) -> list[QueuedItem]:
        """Return up to ``limit`` non-expired payloads, oldest first."""
        self.purge_expired()
        rows = self._conn.execute(
            "SELECT id, payload, created_at FROM queue ORDER BY created_at ASC, id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        items: list[QueuedItem] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                _log.error("corrupt queued payload id=%s; discarding", row["id"])
                self.delete(row["id"])
                continue
            items.append(QueuedItem(id=row["id"], payload=payload, created_at=row["created_at"]))
        return items

    def count(self) -> int:
        """Total buffered payloads (including any not yet purged)."""
        return int(self._conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0])

    # ---- lifecycle ----------------------------------------------------- #
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RetryQueue":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
