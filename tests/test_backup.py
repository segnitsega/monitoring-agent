"""Tests for the pluggable backup checkers."""

from __future__ import annotations

import os
import time

import pytest

import agent.collectors.backup as backup
from agent.collectors.backup import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
    BackupError,
    run_backup_check,
)
from agent.config import BackupConfig


def _set_mtime(path, age_hours: float) -> None:
    when = time.time() - age_hours * 3600
    os.utime(path, (when, when))


# --------------------------- generic_path -------------------------------- #
def test_generic_path_fresh_file_is_success(tmp_path) -> None:
    f = tmp_path / "backup-full-2026-08-20.tar.gz"
    f.write_bytes(b"x" * 2048)
    _set_mtime(f, 1)
    r = backup.check_generic_path({"path": str(f), "freshnessHours": 26})
    assert r["status"] == STATUS_SUCCESS
    assert r["backupType"] == "full"
    assert r["sizeBytes"] == 2048
    assert r["location"] == str(f)
    assert r["lastBackupTime"].endswith("Z")


def test_generic_path_stale_file_is_failed(tmp_path) -> None:
    f = tmp_path / "dump.bak"
    f.write_bytes(b"x")
    _set_mtime(f, 100)
    r = backup.check_generic_path({"path": str(f), "freshnessHours": 26})
    assert r["status"] == STATUS_FAILED


def test_generic_path_dir_picks_newest(tmp_path) -> None:
    old = tmp_path / "incremental-old.tar"
    new = tmp_path / "incremental-new.tar"
    old.write_bytes(b"a")
    new.write_bytes(b"bb")
    _set_mtime(old, 50)
    _set_mtime(new, 1)
    r = backup.check_generic_path({"path": str(tmp_path)})
    assert r["location"] == str(new)
    assert r["backupType"] == "incremental"
    assert r["status"] == STATUS_SUCCESS


def test_generic_path_missing_is_unknown(tmp_path) -> None:
    r = backup.check_generic_path({"path": str(tmp_path / "nope")})
    assert r["status"] == STATUS_UNKNOWN


def test_generic_path_requires_path() -> None:
    with pytest.raises(BackupError):
        backup.check_generic_path({})


# ----------------------------- rsync_log --------------------------------- #
def test_rsync_log_success(tmp_path) -> None:
    log = tmp_path / "rsync.log"
    log.write_text(
        "sending incremental file list\n"
        "sent 1,024 bytes  received 128 bytes  2,304.00 bytes/sec\n"
        "total size is 1,048,576  speedup is 910.22\n"
    )
    r = backup.check_rsync_log({"logPath": str(log), "freshnessHours": 100000})
    assert r["status"] == STATUS_SUCCESS
    assert r["sizeBytes"] == 1048576
    assert r["backupType"] == "incremental"


def test_rsync_log_failure(tmp_path) -> None:
    log = tmp_path / "rsync.log"
    log.write_text(
        "total size is 10  speedup is 1.0\n"
        "rsync error: some files vanished (code 24) at main.c(1234)\n"
    )
    r = backup.check_rsync_log({"logPath": str(log)})
    assert r["status"] == STATUS_FAILED
    assert r["backupType"] is None


def test_rsync_log_missing_is_unknown(tmp_path) -> None:
    r = backup.check_rsync_log({"logPath": str(tmp_path / "absent.log")})
    assert r["status"] == STATUS_UNKNOWN


# ----------------------------- veeam_log --------------------------------- #
def test_veeam_success_incremental(tmp_path) -> None:
    log = tmp_path / "veeam.log"
    log.write_text("Incremental backup job 'DB' finished with Success at 02:00\n")
    r = backup.check_veeam_log({"logPath": str(log), "freshnessHours": 100000})
    assert r["status"] == STATUS_SUCCESS
    assert r["backupType"] == "incremental"


def test_veeam_failed(tmp_path) -> None:
    log = tmp_path / "veeam.log"
    log.write_text("Full backup job 'DB' finished with Failed: repository unreachable\n")
    r = backup.check_veeam_log({"logPath": str(log)})
    assert r["status"] == STATUS_FAILED


# ------------------------ windows_server_backup -------------------------- #
def test_wsb_success(monkeypatch) -> None:
    monkeypatch.setattr(
        backup,
        "_query_wbsummary",
        lambda: {"resultHr": 0, "lastBackupTime": "2026-08-20T02:00:11Z"},
    )
    r = backup.check_windows_server_backup({})
    assert r["status"] == STATUS_SUCCESS
    assert r["lastBackupTime"] == "2026-08-20T02:00:11Z"


def test_wsb_failed(monkeypatch) -> None:
    monkeypatch.setattr(backup, "_query_wbsummary", lambda: {"resultHr": 5})
    assert backup.check_windows_server_backup({})["status"] == STATUS_FAILED


def test_wsb_unavailable_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(backup, "_query_wbsummary", lambda: None)
    assert backup.check_windows_server_backup({})["status"] == STATUS_UNKNOWN


# ------------------------------ dispatcher ------------------------------- #
def test_run_backup_check_none_config() -> None:
    assert run_backup_check(None) is None


def test_run_backup_check_unknown_checker() -> None:
    assert run_backup_check(BackupConfig(checker="does_not_exist")) is None


def test_run_backup_check_traps_misconfig() -> None:
    # generic_path with no 'path' raises BackupError internally -> unknown block
    r = run_backup_check(BackupConfig(checker="generic_path", options={}))
    assert r == backup._result(STATUS_UNKNOWN)


def test_run_backup_check_happy_path(tmp_path) -> None:
    f = tmp_path / "b.tar"
    f.write_bytes(b"data")
    _set_mtime(f, 1)
    r = run_backup_check(BackupConfig(checker="generic_path", options={"path": str(f)}))
    assert r["status"] == STATUS_SUCCESS
    assert r["sizeBytes"] == 4
