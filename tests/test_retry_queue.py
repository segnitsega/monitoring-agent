"""Tests for the SQLite-backed retry queue."""

from __future__ import annotations

import pytest

from agent.transport.retry_queue import RetryQueue


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def queue(tmp_path):
    clock = FakeClock()
    q = RetryQueue(str(tmp_path / "q.db"), retention_hours=24.0, clock=clock)
    q.clock = clock  # expose for tests
    yield q
    q.close()


def test_enqueue_and_peek_oldest_first(queue) -> None:
    clock = queue.clock
    queue.enqueue({"n": 1})
    clock.now += 10
    queue.enqueue({"n": 2})
    items = queue.peek(limit=10)
    assert [i.payload["n"] for i in items] == [1, 2]
    assert queue.count() == 2


def test_delete_removes_item(queue) -> None:
    rid = queue.enqueue({"n": 1})
    queue.delete(rid)
    assert queue.count() == 0
    assert queue.peek() == []


def test_purge_expired_drops_old(queue) -> None:
    clock = queue.clock
    queue.enqueue({"old": True})
    clock.now += 25 * 3600  # 25h later, past the 24h window
    queue.enqueue({"fresh": True})
    dropped = queue.purge_expired()
    assert dropped == 1
    remaining = queue.peek(limit=10)
    assert len(remaining) == 1
    assert remaining[0].payload == {"fresh": True}


def test_peek_purges_before_returning(queue) -> None:
    clock = queue.clock
    queue.enqueue({"old": True})
    clock.now += 25 * 3600
    # peek() calls purge_expired() internally
    assert queue.peek(limit=10) == []
    assert queue.count() == 0


def test_persistence_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "persist.db")
    q1 = RetryQueue(path)
    q1.enqueue({"survives": True})
    q1.close()

    q2 = RetryQueue(path)
    items = q2.peek(limit=10)
    assert len(items) == 1
    assert items[0].payload == {"survives": True}
    q2.close()


def test_corrupt_payload_is_discarded(queue) -> None:
    # Inject a row with invalid JSON directly.
    queue._conn.execute("INSERT INTO queue (payload, created_at) VALUES (?, ?)", ("{bad", 1.0))
    queue._conn.commit()
    queue.enqueue({"good": True})
    items = queue.peek(limit=10)
    assert [i.payload for i in items] == [{"good": True}]


def test_context_manager_closes(tmp_path) -> None:
    with RetryQueue(str(tmp_path / "cm.db")) as q:
        q.enqueue({"a": 1})
        assert q.count() == 1
