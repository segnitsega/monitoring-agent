"""Tests for the Windows service wrapper (runs on any platform)."""

from __future__ import annotations

import agent.service.windows_service as ws


def test_module_imports_without_pywin32() -> None:
    # Verify module imports safely regardless of pywin32 availability.
    if not ws._HAS_PYWIN32:
        assert ws.MonitoringAgentService is None
    else:
        assert ws.MonitoringAgentService is not None
    assert isinstance(ws.DEFAULT_CONFIG_PATH, str)


def test_resolve_config_path_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("MONITORING_AGENT_CONFIG", "D:/cfg/config.json")
    assert ws._resolve_config_path() == "D:/cfg/config.json"


def test_resolve_config_path_default(monkeypatch) -> None:
    monkeypatch.delenv("MONITORING_AGENT_CONFIG", raising=False)
    assert ws._resolve_config_path() == ws.DEFAULT_CONFIG_PATH


def test_main_without_pywin32_reports_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(ws, "_HAS_PYWIN32", False)
    rc = ws.main(["install"])
    assert rc == 1
    assert "pywin32" in capsys.readouterr().err
