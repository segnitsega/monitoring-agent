"""Pluggable backup-evidence checkers (AGENT_SPEC.md §4.2).

The agent never performs backups — it inspects *evidence* left by whatever
backup tool the server already runs (rsync, Veeam, Windows Server Backup, or a
plain output folder) and reports the ``backup`` block of the payload:

    {"status", "lastBackupTime", "backupType", "location", "sizeBytes"}

Each checker is a function ``(options: dict) -> dict`` registered in
``_CHECKERS``. Adding support for a new backup tool means adding one function
and one registry entry — no changes to the collection loop (§9 extensibility).

A checker must never crash the agent: ``run_backup_check`` traps everything and
falls back to a ``unknown`` status block so a single flaky log can't stop health
reporting.
"""

from __future__ import annotations

import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from agent.config import BackupConfig
from agent.logging_setup import get_logger
from agent.timeutils import iso_utc

_log = get_logger()

# Contract status values.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_UNKNOWN = "unknown"

BACKUP_TYPE_FULL = "full"
BACKUP_TYPE_INCREMENTAL = "incremental"

# Cap how much of a (potentially huge, append-only) log we read; the last run's
# result always lives at the tail.
_MAX_LOG_BYTES = 256 * 1024

# Default staleness window before a present-but-old backup is treated as failed.
_DEFAULT_FRESHNESS_HOURS = 26.0


class BackupError(Exception):
    """Raised for a misconfigured checker (e.g. a required option is missing)."""


def _result(
    status: str,
    *,
    last_backup_time: str | None = None,
    backup_type: str | None = None,
    location: str | None = None,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    """Build a contract-shaped backup block."""
    return {
        "status": status,
        "lastBackupTime": last_backup_time,
        "backupType": backup_type,
        "location": location,
        "sizeBytes": size_bytes,
    }


def _infer_type(name: str) -> str | None:
    """Guess Full/Incremental from a filename or log fragment."""
    low = name.lower()
    if "incr" in low:
        return BACKUP_TYPE_INCREMENTAL
    if "full" in low:
        return BACKUP_TYPE_FULL
    return None


def _freshness_hours(options: dict[str, Any]) -> float:
    try:
        return float(options.get("freshnessHours", _DEFAULT_FRESHNESS_HOURS))
    except (TypeError, ValueError):
        return _DEFAULT_FRESHNESS_HOURS


def _is_stale(mtime: float, freshness_hours: float) -> bool:
    return (time.time() - mtime) / 3600.0 > freshness_hours


def _read_tail(path: Path, limit: int = _MAX_LOG_BYTES) -> str:
    """Read up to the last ``limit`` bytes of a text log, tolerant of encoding."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(size - limit)
        data = fh.read()
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# generic_path — newest artifact in a folder (or a single file)
# --------------------------------------------------------------------------- #
def check_generic_path(options: dict[str, Any]) -> dict[str, Any]:
    """Freshness/size of the most recent artifact under a configured path.

    The simplest checker: no log parsing, just "is there a recent-enough file
    where backups are expected?". A present artifact newer than
    ``freshnessHours`` is ``success``; an older one is ``failed`` (backups
    likely stopped running). A missing path is ``unknown`` — we can't tell a
    mistyped path from a genuine outage locally, so we don't cry wolf.
    """
    raw_path = options.get("path")
    if not raw_path or not isinstance(raw_path, str):
        raise BackupError("generic_path checker requires a string 'path' option")

    freshness = _freshness_hours(options)
    root = Path(raw_path)
    if not root.exists():
        _log.warning("backup path %s does not exist", raw_path)
        return _result(STATUS_UNKNOWN, location=raw_path)

    if root.is_dir():
        files = [c for c in root.iterdir() if c.is_file()]
        if not files:
            _log.warning("backup directory %s is empty", raw_path)
            return _result(STATUS_UNKNOWN, location=raw_path)
        newest = max(files, key=lambda c: c.stat().st_mtime)
    else:
        newest = root

    st = newest.stat()
    status = STATUS_FAILED if _is_stale(st.st_mtime, freshness) else STATUS_SUCCESS
    return _result(
        status,
        last_backup_time=iso_utc(st.st_mtime),
        backup_type=_infer_type(newest.name),
        location=str(newest),
        size_bytes=st.st_size,
    )


# --------------------------------------------------------------------------- #
# rsync_log — parse an rsync --stats log file
# --------------------------------------------------------------------------- #
def check_rsync_log(options: dict[str, Any]) -> dict[str, Any]:
    """Parse an rsync log (``--stats``/``--log-file``) for the last run's result.

    Success is inferred from rsync's own completion markers ("total size is N",
    "speedup is"); a trailing "rsync error: ... (code N)" after the last
    completion marker means failure. rsync is a delta transfer, so a successful
    run is reported as ``incremental``. If the log itself hasn't been written
    within ``freshnessHours``, a success is downgraded to failed (the job
    stopped running).
    """
    raw_path = options.get("logPath") or options.get("path")
    if not raw_path or not isinstance(raw_path, str):
        raise BackupError("rsync_log checker requires a string 'logPath' option")

    log_path = Path(raw_path)
    if not log_path.is_file():
        _log.warning("rsync log %s not found", raw_path)
        return _result(STATUS_UNKNOWN, location=raw_path)

    text = _read_tail(log_path)
    err_idx = text.rfind("rsync error")
    ok_idx = max(text.rfind("total size is"), text.rfind("speedup is"))

    if err_idx != -1 and err_idx > ok_idx:
        status = STATUS_FAILED
    elif ok_idx != -1:
        status = STATUS_SUCCESS
    else:
        status = STATUS_UNKNOWN

    sizes = re.findall(r"total size is ([\d,]+)", text)
    size_bytes = int(sizes[-1].replace(",", "")) if sizes else None

    mtime = log_path.stat().st_mtime
    if status == STATUS_SUCCESS and _is_stale(mtime, _freshness_hours(options)):
        _log.warning("rsync log %s is stale; downgrading to failed", raw_path)
        status = STATUS_FAILED

    return _result(
        status,
        last_backup_time=iso_utc(mtime),
        backup_type=BACKUP_TYPE_INCREMENTAL if status == STATUS_SUCCESS else None,
        location=str(log_path),
        size_bytes=size_bytes,
    )


# --------------------------------------------------------------------------- #
# veeam_log — parse a Veeam job log / session summary
# --------------------------------------------------------------------------- #
def check_veeam_log(options: dict[str, Any]) -> dict[str, Any]:
    """Parse a Veeam job log for the session result and backup type.

    Veeam reports "finished with Success / Warning / Failed". A warning still
    means the job completed, so it maps to ``success``; "failed"/"error" map to
    ``failed``; an in-flight session maps to ``in_progress``.
    """
    raw_path = options.get("logPath") or options.get("path")
    if not raw_path or not isinstance(raw_path, str):
        raise BackupError("veeam_log checker requires a string 'logPath' option")

    log_path = Path(raw_path)
    if not log_path.is_file():
        _log.warning("veeam log %s not found", raw_path)
        return _result(STATUS_UNKNOWN, location=raw_path)

    text = _read_tail(log_path)
    low = text.lower()

    if re.search(r"\bfailed\b|\berror\b", low):
        status = STATUS_FAILED
    elif re.search(r"in progress|\brunning\b|\bstarted\b", low) and "finished" not in low:
        status = STATUS_IN_PROGRESS
    elif "warning" in low or "success" in low:
        status = STATUS_SUCCESS
    else:
        status = STATUS_UNKNOWN

    backup_type = _infer_type(low)
    mtime = log_path.stat().st_mtime
    if status == STATUS_SUCCESS and _is_stale(mtime, _freshness_hours(options)):
        _log.warning("veeam log %s is stale; downgrading to failed", raw_path)
        status = STATUS_FAILED

    return _result(
        status,
        last_backup_time=iso_utc(mtime),
        backup_type=backup_type,
        location=str(log_path),
    )


# --------------------------------------------------------------------------- #
# windows_server_backup — query Windows Server Backup summary
# --------------------------------------------------------------------------- #
def _query_wbsummary() -> dict[str, Any] | None:
    """Return {'resultHr', 'lastBackupTime', 'location'} from Windows Server Backup.

    Uses PowerShell ``Get-WBSummary``. Returns ``None`` on non-Windows hosts or
    when the feature/module is unavailable, so the checker degrades to
    ``unknown`` rather than failing. Isolated here so the parsing/mapping in
    :func:`check_windows_server_backup` can be unit-tested without Windows.
    """
    if platform.system() != "Windows":  # pragma: no cover - platform gated
        return None
    ps = (  # pragma: no cover - requires Windows
        "$s = Get-WBSummary; "
        "[pscustomobject]@{"
        "resultHr=$s.LastBackupResultHR; "
        "lastBackupTime=($s.LastSuccessfulBackupTime).ToUniversalTime()"
        ".ToString('yyyy-MM-ddTHH:mm:ssZ')"
        "} | ConvertTo-Json -Compress"
    )
    try:  # pragma: no cover - requires Windows
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        import json

        return json.loads(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:  # pragma: no cover
        _log.warning("Get-WBSummary query failed: %s", exc)
        return None


def check_windows_server_backup(options: dict[str, Any]) -> dict[str, Any]:
    """Map a Windows Server Backup summary to a contract backup block."""
    summary = _query_wbsummary()
    if not summary:
        _log.warning("windows_server_backup: no summary available on this host")
        return _result(STATUS_UNKNOWN)

    result_hr = summary.get("resultHr")
    status = STATUS_SUCCESS if result_hr == 0 else STATUS_FAILED
    return _result(
        status,
        last_backup_time=summary.get("lastBackupTime"),
        backup_type=None,
        location=summary.get("location"),
    )


# --------------------------------------------------------------------------- #
# Registry + dispatcher
# --------------------------------------------------------------------------- #
_CHECKERS = {
    "generic_path": check_generic_path,
    "rsync_log": check_rsync_log,
    "veeam_log": check_veeam_log,
    "windows_server_backup": check_windows_server_backup,
}

KNOWN_CHECKERS = frozenset(_CHECKERS)


def run_backup_check(cfg: BackupConfig | None) -> dict[str, Any] | None:
    """Run the configured checker, returning the backup block (or ``None``).

    ``None`` means "no checker configured" (the payload's backup field is
    ``null``). A configured-but-failing checker never returns ``None`` and never
    raises — it degrades to an ``unknown`` status block so health reporting
    continues regardless.
    """
    if cfg is None:
        return None

    checker = _CHECKERS.get(cfg.checker)
    if checker is None:
        _log.error("unknown backup checker %r; reporting null backup block", cfg.checker)
        return None

    try:
        return checker(cfg.options)
    except BackupError as exc:
        _log.error("backup checker %s misconfigured: %s", cfg.checker, exc)
        return _result(STATUS_UNKNOWN)
    except Exception:  # noqa: BLE001 - a checker must never take down the agent
        _log.exception("unexpected error in backup checker %s", cfg.checker)
        return _result(STATUS_UNKNOWN)
