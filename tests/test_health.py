"""Tests for agent.collectors.health (psutil fully mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent.collectors.health as health


@pytest.fixture
def fake_psutil(monkeypatch):
    monkeypatch.setattr(
        health.psutil, "virtual_memory", lambda: SimpleNamespace(total=100, used=40, percent=40.0)
    )
    monkeypatch.setattr(
        health.psutil, "net_io_counters", lambda: SimpleNamespace(bytes_sent=10, bytes_recv=20)
    )
    monkeypatch.setattr(health.psutil, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(health.psutil, "cpu_percent", lambda interval=None: 42.34)
    monkeypatch.setattr(
        health.psutil, "disk_usage", lambda p: SimpleNamespace(total=500, used=210, percent=42.0)
    )
    monkeypatch.setattr(
        health.psutil, "net_if_stats", lambda: {"eth0": SimpleNamespace(isup=True)}
    )


def test_collect_health_shape(fake_psutil) -> None:
    snap = health.collect_health(["/"], cpu_sample_seconds=0)
    assert snap["cpuUsagePercent"] == 42.3  # rounded to 1dp
    assert snap["memory"] == {"totalBytes": 100, "usedBytes": 40, "usagePercent": 40.0}
    assert snap["disk"] == [
        {"path": "/", "totalBytes": 500, "usedBytes": 210, "usagePercent": 42.0}
    ]
    assert snap["network"] == {"bytesSent": 10, "bytesRecv": 20, "isReachable": True}
    assert snap["uptimeSeconds"] >= 0
    assert snap["lastBootTime"].endswith("Z")


def test_unavailable_disk_path_is_skipped(fake_psutil, monkeypatch) -> None:
    def boom(path):
        if path == "/bad":
            raise OSError("no such mount")
        return SimpleNamespace(total=1, used=0, percent=0.0)

    monkeypatch.setattr(health.psutil, "disk_usage", boom)
    snap = health.collect_health(["/bad", "/"])
    assert [d["path"] for d in snap["disk"]] == ["/"]


def test_is_network_up_ignores_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        health.psutil, "net_if_stats", lambda: {"lo": SimpleNamespace(isup=True)}
    )
    assert health.is_network_up() is False
    monkeypatch.setattr(
        health.psutil,
        "net_if_stats",
        lambda: {"lo": SimpleNamespace(isup=True), "eth0": SimpleNamespace(isup=True)},
    )
    assert health.is_network_up() is True
