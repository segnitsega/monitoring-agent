"""Tests for bootstrap registration (POST /api/v1/agent/register)."""

from __future__ import annotations

import json

import pytest
import requests

from agent.config import parse_config
from agent.transport.register import RegistrationError, ensure_registered, register_agent


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text="{}") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, *, response=None, exc=None) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return self._response


def _config(**over):
    raw = {
        "backendUrl": "https://portal.example.com",
        "registerSecret": "shared-secret",
        "hostname": "finance-app.internal.local",
    }
    raw.update(over)
    return parse_config(raw)


def _issued(**over):
    data = {
        "serverId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "token": "issued-per-server-token",
        "hostname": "finance-app.internal.local",
        "os": "LINUX",
        "intervalSeconds": 60,
    }
    data.update(over)
    return {"success": True, "data": data}


def test_register_posts_secret_and_hostname_without_bearer() -> None:
    session = FakeSession(response=FakeResponse(200, _issued()))
    issued = register_agent(_config(), session=session)
    assert issued["token"] == "issued-per-server-token"
    assert issued["server_id"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    call = session.calls[0]
    assert call["url"] == "https://portal.example.com/api/v1/agent/register"
    assert call["json"] == {
        "secret": "shared-secret",
        "hostname": "finance-app.internal.local",
    }
    assert "Authorization" not in call["headers"]
    assert call["timeout"] == 10.0


def test_register_errors_do_not_include_secrets() -> None:
    session = FakeSession(response=FakeResponse(401, {"success": False}))
    with pytest.raises(RegistrationError, match="invalid secret") as exc:
        register_agent(_config(), session=session)
    assert "shared-secret" not in str(exc.value)
    assert "issued-per-server-token" not in str(exc.value)


@pytest.mark.parametrize(
    "status,match",
    [
        (401, "invalid secret"),
        (403, "inactive"),
        (404, "inventory"),
        (500, "HTTP 500"),
    ],
)
def test_register_http_errors(status, match) -> None:
    session = FakeSession(response=FakeResponse(status, {"success": False}))
    with pytest.raises(RegistrationError, match=match):
        register_agent(_config(), session=session)


def test_register_network_error() -> None:
    session = FakeSession(exc=requests.ConnectionError("down"))
    with pytest.raises(RegistrationError, match="registration request failed"):
        register_agent(_config(), session=session)


def test_ensure_registered_is_noop_when_token_present(tmp_path) -> None:
    cfg = parse_config(
        {
            "serverId": "srv-1",
            "token": "already-issued",
            "backendUrl": "https://portal.example.com",
        }
    )
    session = FakeSession(response=FakeResponse(200, _issued()))
    out = ensure_registered(cfg, tmp_path / "config.json", session=session)
    assert session.calls == []
    assert out.token == "already-issued"
    assert out is cfg


def test_ensure_registered_persists_issued_credentials(tmp_path) -> None:
    path = tmp_path / "config.json"
    raw = {
        "backendUrl": "https://portal.example.com",
        "registerSecret": "shared-secret",
        "hostname": "finance-app.internal.local",
        "logLevel": "ERROR",
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    cfg = parse_config(raw)
    session = FakeSession(response=FakeResponse(200, _issued(intervalSeconds=45)))

    out = ensure_registered(cfg, path, session=session)

    assert out.needs_registration is False
    assert out.token == "issued-per-server-token"
    assert out.server_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert out.interval_seconds == 45
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["token"] == "issued-per-server-token"
    assert saved["serverId"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert saved["intervalSeconds"] == 45
    assert saved["registerSecret"] == "shared-secret"
