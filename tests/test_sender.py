"""Tests for the HTTPS sender and its outcome classification."""

from __future__ import annotations

import pytest
import requests

from agent.transport.sender import Sender, SendOutcome


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


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


def _sender(session) -> Sender:
    return Sender("https://portal.example.com/api/v1/health", "tok-123", session=session)


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_2xx_delivered(status) -> None:
    s = _sender(FakeSession(response=FakeResponse(status)))
    assert s.send({"a": 1}) is SendOutcome.DELIVERED


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_drop(status) -> None:
    s = _sender(FakeSession(response=FakeResponse(status)))
    assert s.send({"a": 1}) is SendOutcome.DROP_AUTH


@pytest.mark.parametrize("status", [400, 404, 422])
def test_other_4xx_drop_client(status) -> None:
    s = _sender(FakeSession(response=FakeResponse(status)))
    assert s.send({"a": 1}) is SendOutcome.DROP_CLIENT


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_retry(status) -> None:
    s = _sender(FakeSession(response=FakeResponse(status)))
    assert s.send({"a": 1}) is SendOutcome.RETRY


@pytest.mark.parametrize("exc", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_network_errors_retry(exc) -> None:
    s = _sender(FakeSession(exc=exc))
    assert s.send({"a": 1}) is SendOutcome.RETRY


def test_request_shape_and_auth_header() -> None:
    session = FakeSession(response=FakeResponse(200))
    s = _sender(session)
    s.send({"serverId": "srv-1"})
    call = session.calls[0]
    assert call["url"] == "https://portal.example.com/api/v1/health"
    assert call["json"] == {"serverId": "srv-1"}
    assert call["headers"]["Authorization"] == "Bearer tok-123"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] == 10.0
