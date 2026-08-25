"""Tests for the systemd unit generation."""

from __future__ import annotations

from agent.service.linux_systemd import build_unit, main


def test_unit_contains_key_directives() -> None:
    unit = build_unit("/opt/agent/monitoring-agent", "/etc/monitoring-agent/config.json")
    assert "ExecStart=/opt/agent/monitoring-agent --config /etc/monitoring-agent/config.json" in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "After=network-online.target" in unit
    # hardening present
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/monitoring-agent /etc/monitoring-agent" in unit


def test_unit_defaults() -> None:
    unit = build_unit()
    assert "ExecStart=/usr/local/bin/monitoring-agent --config /etc/monitoring-agent/config.json" in unit
    assert "User=monitoring-agent" in unit


def test_render_subcommand_prints_unit(capsys) -> None:
    rc = main(["render", "--exec", "/x/agent", "--config", "/x/config.json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ExecStart=/x/agent --config /x/config.json" in out
