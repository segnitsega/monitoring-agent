"""Load and validate the agent's on-disk configuration (``config.json``).

The config file holds the shared registration secret (until a per-server token
is issued) and then the raw per-server token. It is expected to be created at
install time with restricted permissions (``chmod 600`` on Linux, ACL-restricted
on Windows). This module never logs the token or the registration secret.
"""

from __future__ import annotations

import json
import os
import platform
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETENTION_HOURS = 24.0
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_QUEUE_PATH = "retry_queue.db"

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_PLACEHOLDER_TOKENS = {
    "",
    "REPLACE_WITH_PER_SERVER_TOKEN_ISSUED_AT_REGISTRATION",
    "REPLACE_WITH_PER_SERVER_TOKEN",
}
_PLACEHOLDER_SECRETS = {
    "",
    "REPLACE_WITH_SHARED_AGENT_REGISTER_SECRET",
    "REPLACE_WITH_AGENT_REGISTER_SECRET",
}


class ConfigError(Exception):
    """Raised when ``config.json`` is missing, unreadable, or invalid."""


def _default_disk_paths() -> list[str]:
    return ["C:\\"] if platform.system() == "Windows" else ["/"]


@dataclass(frozen=True)
class BackupConfig:
    """Configuration for a single pluggable backup checker."""

    checker: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentConfig:
    """Validated, typed view of ``config.json``."""

    server_id: str
    token: str
    backend_url: str
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    disk_paths: list[str] = field(default_factory=_default_disk_paths)
    retry_retention_hours: float = DEFAULT_RETENTION_HOURS
    queue_path: str = DEFAULT_QUEUE_PATH
    log_file: str | None = None
    log_level: str = DEFAULT_LOG_LEVEL
    backup: BackupConfig | None = None
    hostname: str = ""
    register_secret: str = ""

    @property
    def health_endpoint(self) -> str:
        """Full ingestion URL derived from the configured base URL."""
        return self.backend_url.rstrip("/") + "/api/v1/health"

    @property
    def register_endpoint(self) -> str:
        """Bootstrap URL used when this host has no per-server token yet."""
        return self.backend_url.rstrip("/") + "/api/v1/agent/register"

    @property
    def needs_registration(self) -> bool:
        """True when the agent must call ``/api/v1/agent/register`` before ingest."""
        return not self.token

    def with_credentials(
        self,
        *,
        server_id: str,
        token: str,
        interval_seconds: int | None = None,
        hostname: str | None = None,
    ) -> AgentConfig:
        """Return a copy with the credentials issued at registration."""
        updates: dict[str, Any] = {"server_id": server_id, "token": token}
        if interval_seconds is not None:
            updates["interval_seconds"] = int(interval_seconds)
        if hostname:
            updates["hostname"] = hostname
        return replace(self, **updates)


def _require_str(raw: dict[str, Any], key: str, errors: list[str]) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"'{key}' is required and must be a non-empty string")
        return ""
    return value.strip()


def _optional_str(raw: dict[str, Any], key: str, errors: list[str]) -> str:
    if key not in raw or raw[key] is None:
        return ""
    value = raw[key]
    if not isinstance(value, str):
        errors.append(f"'{key}' must be a string")
        return ""
    return value.strip()


def _optional_number(
    raw: dict[str, Any], key: str, default: float, errors: list[str], *, minimum: float
) -> float:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"'{key}' must be a number")
        return default
    if value < minimum:
        errors.append(f"'{key}' must be >= {minimum}")
        return default
    return float(value)


def _parse_backup(raw: dict[str, Any], errors: list[str]) -> BackupConfig | None:
    block = raw.get("backup")
    if block is None:
        return None
    if not isinstance(block, dict):
        errors.append("'backup' must be an object or null")
        return None
    checker = block.get("checker")
    if not isinstance(checker, str) or not checker.strip():
        errors.append("'backup.checker' is required when 'backup' is set")
        return None
    options = block.get("options", {})
    if not isinstance(options, dict):
        errors.append("'backup.options' must be an object")
        options = {}
    return BackupConfig(checker=checker.strip(), options=options)


def parse_config(raw: dict[str, Any]) -> AgentConfig:
    """Validate a raw config mapping and return a typed :class:`AgentConfig`.

    Raises :class:`ConfigError` listing *all* problems found, so an operator can
    fix the file in one pass rather than one error at a time.
    """
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")

    errors: list[str] = []

    backend_url = _require_str(raw, "backendUrl", errors)
    token = _optional_str(raw, "token", errors)
    if token in _PLACEHOLDER_TOKENS:
        token = ""
    server_id = _optional_str(raw, "serverId", errors)
    hostname = _optional_str(raw, "hostname", errors)
    register_secret = _optional_str(raw, "registerSecret", errors)
    if register_secret in _PLACEHOLDER_SECRETS:
        register_secret = ""

    if backend_url and not backend_url.startswith(("http://", "https://")):
        errors.append("'backendUrl' must start with http:// or https://")
    if token and not server_id:
        errors.append("'serverId' is required when 'token' is set")
    if not token and not register_secret:
        errors.append(
            "'token' is missing — set a per-server token, or set 'registerSecret' "
            "so the agent can register on first start"
        )

    interval = _optional_number(raw, "intervalSeconds", DEFAULT_INTERVAL_SECONDS, errors, minimum=1)
    timeout = _optional_number(raw, "timeoutSeconds", DEFAULT_TIMEOUT_SECONDS, errors, minimum=1)
    retention = _optional_number(
        raw, "retryRetentionHours", DEFAULT_RETENTION_HOURS, errors, minimum=0
    )

    disk_paths = raw.get("diskPaths", _default_disk_paths())
    if not isinstance(disk_paths, list) or not all(isinstance(p, str) for p in disk_paths):
        errors.append("'diskPaths' must be a list of strings")
        disk_paths = _default_disk_paths()
    if isinstance(disk_paths, list) and len(disk_paths) == 0:
        errors.append("'diskPaths' must contain at least one path")

    log_level = raw.get("logLevel", DEFAULT_LOG_LEVEL)
    if not isinstance(log_level, str) or log_level.upper() not in _VALID_LOG_LEVELS:
        errors.append(f"'logLevel' must be one of {sorted(_VALID_LOG_LEVELS)}")
        log_level = DEFAULT_LOG_LEVEL

    log_file = raw.get("logFile")
    if log_file is not None and not isinstance(log_file, str):
        errors.append("'logFile' must be a string or null")
        log_file = None

    queue_path = raw.get("queuePath", DEFAULT_QUEUE_PATH)
    if not isinstance(queue_path, str) or not queue_path.strip():
        errors.append("'queuePath' must be a non-empty string")
        queue_path = DEFAULT_QUEUE_PATH

    backup = _parse_backup(raw, errors)

    if errors:
        raise ConfigError("invalid config.json:\n  - " + "\n  - ".join(errors))

    return AgentConfig(
        server_id=server_id,
        token=token,
        backend_url=backend_url,
        interval_seconds=int(interval),
        timeout_seconds=timeout,
        disk_paths=list(disk_paths),
        retry_retention_hours=retention,
        queue_path=queue_path,
        log_file=log_file,
        log_level=log_level.upper(),
        backup=backup,
        hostname=hostname,
        register_secret=register_secret,
    )


def load_config(path: str | Path) -> AgentConfig:
    """Read, parse and validate ``config.json`` at ``path``."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {p}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {p}: {exc}") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {p} is not valid JSON: {exc}") from exc

    return parse_config(raw)


def persist_credentials(
    path: str | Path,
    *,
    server_id: str,
    token: str,
    interval_seconds: int | None = None,
    hostname: str | None = None,
) -> None:
    """Write issued ``serverId`` / ``token`` back to ``config.json`` without logging them."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not update config file {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {p} is not a JSON object")

    raw["serverId"] = server_id
    raw["token"] = token
    if interval_seconds is not None:
        raw["intervalSeconds"] = int(interval_seconds)
    if hostname:
        raw["hostname"] = hostname

    tmp = p.parent / f".{p.name}.tmp"
    try:
        tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, stat.S_IMODE(p.stat().st_mode))
        except OSError:
            pass
        os.replace(tmp, p)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"could not write credentials to {p}: {exc}") from exc


def is_permission_secure(path: str | Path) -> bool:
    """Return ``True`` if the config file is not readable by group/other (POSIX).

    Always returns ``True`` on Windows, where file security is governed by ACLs
    rather than POSIX mode bits (the installer restricts the ACL instead).
    """
    if platform.system() == "Windows":
        return True
    try:
        mode = Path(path).stat().st_mode
    except OSError:
        return True
    return not bool(mode & (stat.S_IRWXG | stat.S_IRWXO))
