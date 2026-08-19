"""Tests for agent.payload.build_payload."""

from __future__ import annotations

import re

from agent.payload import build_payload

_HEALTH = {"cpuUsagePercent": 1.0}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_payload_has_all_contract_keys() -> None:
    p = build_payload("srv-1", _HEALTH, None)
    assert set(p) == {"serverId", "hostname", "os", "timestamp", "health", "backup"}
    assert p["serverId"] == "srv-1"
    assert p["health"] is _HEALTH
    assert p["backup"] is None
    assert _ISO.match(p["timestamp"])


def test_backup_block_passthrough() -> None:
    backup = {"status": "success", "sizeBytes": 42}
    p = build_payload("srv-1", _HEALTH, backup)
    assert p["backup"] == backup


def test_explicit_timestamp_used() -> None:
    p = build_payload("srv-1", _HEALTH, None, timestamp="2026-08-19T00:00:00Z")
    assert p["timestamp"] == "2026-08-19T00:00:00Z"
