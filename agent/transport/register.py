"""Bootstrap registration: exchange the shared secret for a per-server token.

Called only when ``config.json`` has no usable ``token``. POSTs to
``/api/v1/agent/register`` and persists the issued credentials so later cycles
use ``/api/v1/health`` with ``Authorization: Bearer <token>``.

The secret and token are never logged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from agent.collectors.health import get_hostname
from agent.config import AgentConfig, ConfigError, persist_credentials
from agent.logging_setup import get_logger

_log = get_logger()


class RegistrationError(Exception):
    """Raised when bootstrap registration cannot complete."""


def _registration_hostname(config: AgentConfig) -> str:
    hostname = config.hostname.strip() or get_hostname()
    if not hostname or hostname == "unknown":
        raise RegistrationError(
            "hostname is required for registration (set 'hostname' in config.json "
            "to the inventory ipOrHostname)"
        )
    return hostname


def _parse_register_response(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise RegistrationError("registration response was not a JSON object")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RegistrationError("registration response missing data")

    token = data.get("token")
    server_id = data.get("serverId")
    if not isinstance(token, str) or not token.strip():
        raise RegistrationError("registration response missing token")
    if not isinstance(server_id, str) or not server_id.strip():
        raise RegistrationError("registration response missing serverId")

    interval = data.get("intervalSeconds")
    interval_seconds: int | None = None
    if isinstance(interval, bool):
        interval_seconds = None
    elif isinstance(interval, (int, float)) and interval >= 1:
        interval_seconds = int(interval)

    hostname = data.get("hostname")
    returned_hostname = hostname.strip() if isinstance(hostname, str) and hostname.strip() else None

    return {
        "token": token.strip(),
        "server_id": server_id.strip(),
        "interval_seconds": interval_seconds,
        "hostname": returned_hostname,
    }


def register_agent(
    config: AgentConfig,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """POST the shared secret + hostname; return issued credentials (never logged)."""
    hostname = _registration_hostname(config)
    client = session or requests.Session()
    try:
        response = client.post(
            config.register_endpoint,
            json={"secret": config.register_secret, "hostname": hostname},
            headers={"Content-Type": "application/json"},
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise RegistrationError(f"registration request failed: {exc}") from exc

    status = response.status_code
    if status == 401:
        raise RegistrationError("registration rejected (invalid secret)")
    if status == 403:
        raise RegistrationError("registration rejected (server is inactive)")
    if status == 404:
        raise RegistrationError(
            "registration rejected (hostname not in inventory); "
            "create the server first and match ipOrHostname"
        )
    if not 200 <= status < 300:
        raise RegistrationError(f"registration failed (HTTP {status})")

    try:
        body = response.json()
    except ValueError as exc:
        raise RegistrationError("registration response was not JSON") from exc

    issued = _parse_register_response(body)
    if issued["hostname"] is None:
        issued["hostname"] = hostname
    return issued


def ensure_registered(
    config: AgentConfig,
    config_path: str | Path,
    *,
    session: requests.Session | None = None,
) -> AgentConfig:
    """If there is no per-server token, register and persist one; otherwise no-op."""
    if not config.needs_registration:
        return config
    if not config.register_secret:
        raise RegistrationError(
            "no per-server token and no registerSecret; cannot register this agent"
        )

    hostname = _registration_hostname(config)
    _log.info("no per-server token; registering agent (hostname=%s)", hostname)
    issued = register_agent(config, session=session)

    try:
        persist_credentials(
            config_path,
            server_id=issued["server_id"],
            token=issued["token"],
            interval_seconds=issued["interval_seconds"],
            hostname=issued["hostname"],
        )
    except ConfigError as exc:
        raise RegistrationError(str(exc)) from exc

    _log.info("agent registered (server=%s hostname=%s)", issued["server_id"], issued["hostname"])
    return config.with_credentials(
        server_id=issued["server_id"],
        token=issued["token"],
        interval_seconds=issued["interval_seconds"],
        hostname=issued["hostname"],
    )
