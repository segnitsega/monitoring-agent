"""Tests for agent.config: parsing, validation and defaults."""

from __future__ import annotations

import json

import pytest

from agent.config import AgentConfig, ConfigError, load_config, parse_config, persist_credentials

VALID = {
    "serverId": "srv-1029",
    "token": "real-token-abc123",
    "backendUrl": "https://portal.example.com",
}


def test_parse_minimal_applies_defaults() -> None:
    cfg = parse_config(dict(VALID))
    assert isinstance(cfg, AgentConfig)
    assert cfg.server_id == "srv-1029"
    assert cfg.interval_seconds == 60
    assert cfg.timeout_seconds == 10.0
    assert cfg.retry_retention_hours == 24.0
    assert cfg.disk_paths  # non-empty platform default
    assert cfg.backup is None


def test_health_endpoint_is_derived_and_strips_trailing_slash() -> None:
    cfg = parse_config({**VALID, "backendUrl": "https://portal.example.com/"})
    assert cfg.health_endpoint == "https://portal.example.com/api/v1/health"


def test_missing_required_fields_raise_and_list_all() -> None:
    with pytest.raises(ConfigError) as exc:
        parse_config({})
    message = str(exc.value)
    assert "backendUrl" in message
    assert "token" in message
    assert "registerSecret" in message


def test_placeholder_token_is_treated_as_missing() -> None:
    with pytest.raises(ConfigError, match="registerSecret"):
        parse_config({**VALID, "token": "REPLACE_WITH_PER_SERVER_TOKEN"})


def test_missing_token_ok_when_register_secret_present() -> None:
    cfg = parse_config(
        {
            "backendUrl": "https://portal.example.com",
            "registerSecret": "shared-secret",
            "hostname": "finance-app.internal.local",
        }
    )
    assert cfg.needs_registration is True
    assert cfg.token == ""
    assert cfg.server_id == ""
    assert cfg.register_secret == "shared-secret"
    assert cfg.hostname == "finance-app.internal.local"
    assert cfg.register_endpoint == "https://portal.example.com/api/v1/agent/register"


def test_placeholder_token_plus_register_secret_registers() -> None:
    cfg = parse_config(
        {
            "backendUrl": "https://portal.example.com",
            "token": "REPLACE_WITH_PER_SERVER_TOKEN_ISSUED_AT_REGISTRATION",
            "registerSecret": "shared-secret",
        }
    )
    assert cfg.needs_registration is True
    assert cfg.token == ""


def test_token_without_server_id_rejected() -> None:
    with pytest.raises(ConfigError, match="serverId"):
        parse_config({"backendUrl": "https://portal.example.com", "token": "real-token"})


def test_non_http_backend_url_rejected() -> None:
    with pytest.raises(ConfigError, match="backendUrl"):
        parse_config({**VALID, "backendUrl": "ftp://nope"})


def test_bad_interval_and_types_rejected() -> None:
    with pytest.raises(ConfigError, match="intervalSeconds"):
        parse_config({**VALID, "intervalSeconds": 0})
    with pytest.raises(ConfigError, match="diskPaths"):
        parse_config({**VALID, "diskPaths": "not-a-list"})
    with pytest.raises(ConfigError, match="logLevel"):
        parse_config({**VALID, "logLevel": "LOUD"})


def test_backup_block_parsed() -> None:
    cfg = parse_config(
        {**VALID, "backup": {"checker": "generic_path", "options": {"path": "/backups"}}}
    )
    assert cfg.backup is not None
    assert cfg.backup.checker == "generic_path"
    assert cfg.backup.options == {"path": "/backups"}


def test_backup_without_checker_rejected() -> None:
    with pytest.raises(ConfigError, match="backup.checker"):
        parse_config({**VALID, "backup": {"options": {}}})


def test_load_config_from_file(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**VALID, "intervalSeconds": 30}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.interval_seconds == 30


def test_load_missing_file_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path/config.json")


def test_load_invalid_json_raises(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


def test_persist_credentials_updates_file_and_keeps_other_keys(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "backendUrl": "https://portal.example.com",
                "registerSecret": "shared-secret",
                "hostname": "host.example",
                "logLevel": "DEBUG",
            }
        ),
        encoding="utf-8",
    )
    persist_credentials(
        path,
        server_id="uuid-1",
        token="issued-token",
        interval_seconds=45,
        hostname="host.example",
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["serverId"] == "uuid-1"
    assert raw["token"] == "issued-token"
    assert raw["intervalSeconds"] == 45
    assert raw["registerSecret"] == "shared-secret"
    assert raw["logLevel"] == "DEBUG"
