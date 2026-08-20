"""Tests for the AgentRunner deliver/buffer/flush policy and CLI."""

from __future__ import annotations

import pytest

import agent.main as main_mod
from agent.config import parse_config
from agent.main import AgentRunner, main
from agent.transport.retry_queue import RetryQueue
from agent.transport.sender import SendOutcome


class FakeSender:
    def __init__(self, outcomes=None) -> None:
        self.outcomes = list(outcomes or [])
        self.sent: list[dict] = []

    def send(self, payload):
        self.sent.append(payload)
        return self.outcomes.pop(0) if self.outcomes else SendOutcome.DELIVERED


def _config(tmp_path, **over):
    raw = {
        "serverId": "srv-1",
        "token": "real-token",
        "backendUrl": "https://portal.example.com",
        "queuePath": str(tmp_path / "q.db"),
    }
    raw.update(over)
    return parse_config(raw)


def _runner(tmp_path, sender):
    cfg = _config(tmp_path)
    queue = RetryQueue(cfg.queue_path)
    return AgentRunner(cfg, sender=sender, queue=queue), queue


def test_delivered_then_flushes_queue(tmp_path) -> None:
    sender = FakeSender([SendOutcome.DELIVERED, SendOutcome.DELIVERED, SendOutcome.DELIVERED])
    runner, queue = _runner(tmp_path, sender)
    queue.enqueue({"buffered": 1})
    queue.enqueue({"buffered": 2})
    runner._deliver({"live": True})
    assert queue.count() == 0
    assert len(sender.sent) == 3  # live + 2 flushed


def test_retry_buffers_payload(tmp_path) -> None:
    sender = FakeSender([SendOutcome.RETRY])
    runner, queue = _runner(tmp_path, sender)
    runner._deliver({"live": True})
    assert queue.count() == 1
    assert queue.peek()[0].payload == {"live": True}


def test_auth_failure_drops_without_buffering(tmp_path) -> None:
    sender = FakeSender([SendOutcome.DROP_AUTH])
    runner, queue = _runner(tmp_path, sender)
    runner._deliver({"live": True})
    assert queue.count() == 0


def test_flush_stops_on_retry(tmp_path) -> None:
    # live delivered -> flush; first queued delivers, second must retry -> stop.
    sender = FakeSender([SendOutcome.DELIVERED, SendOutcome.DELIVERED, SendOutcome.RETRY])
    runner, queue = _runner(tmp_path, sender)
    queue.enqueue({"q": 1})
    queue.enqueue({"q": 2})
    runner._deliver({"live": True})
    assert queue.count() == 1
    assert queue.peek()[0].payload == {"q": 2}


def test_run_cycle_builds_and_sends(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "collect_health", lambda paths: {"cpuUsagePercent": 5.0})
    monkeypatch.setattr(main_mod, "run_backup_check", lambda cfg: None)
    sender = FakeSender([SendOutcome.DELIVERED])
    runner, _ = _runner(tmp_path, sender)
    runner.run_cycle()
    sent = sender.sent[0]
    assert sent["serverId"] == "srv-1"
    assert sent["health"] == {"cpuUsagePercent": 5.0}
    assert sent["backup"] is None


def test_cli_check_config_ok(tmp_path, capsys) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        '{"serverId":"srv-1","token":"real","backendUrl":"https://x.example.com",'
        f'"queuePath":"{tmp_path / "q.db"}","logLevel":"ERROR"}}'
    )
    rc = main(["--config", str(cfg_file), "--check-config"])
    assert rc == 0


def test_cli_bad_config_returns_2(tmp_path) -> None:
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text('{"serverId":"srv-1"}')  # missing required fields
    rc = main(["--config", str(cfg_file), "--check-config"])
    assert rc == 2


def test_cli_version(capsys) -> None:
    rc = main(["--version"])
    assert rc == 0
    assert capsys.readouterr().out.strip()
