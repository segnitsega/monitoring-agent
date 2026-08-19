"""Host health metric collection via psutil.

Produces the ``health`` block of the ingestion payload (AGENT_SPEC.md §4.1/§6):
CPU, memory, per-mount disk usage, network counters + reachability, uptime and
last boot time. Everything here is read-only and cheap so the agent keeps its
sub-1% CPU footprint on the monitored server.
"""

from __future__ import annotations

import platform
import socket
import time
from typing import Any

import psutil

from agent.logging_setup import get_logger
from agent.timeutils import iso_utc

_log = get_logger()

# Interface-name prefixes treated as loopback and ignored for reachability.
_LOOPBACK_PREFIXES = ("lo", "loopback")


def get_hostname() -> str:
    """Best-effort hostname; never raises."""
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover - platform dependent
        return "unknown"


def get_os() -> str:
    """OS family name, e.g. ``Linux`` or ``Windows``."""
    return platform.system() or "unknown"


def is_network_up() -> bool:
    """Simple 'is the network up' flag: any non-loopback interface up?

    Deliberately avoids reaching out to the backend — that reachability is
    already proven (or not) by the POST itself. This flag reflects the host's
    own link state only.
    """
    try:
        stats = psutil.net_if_stats()
    except Exception:  # pragma: no cover - defensive
        return False
    for name, st in stats.items():
        if name.lower().startswith(_LOOPBACK_PREFIXES):
            continue
        if getattr(st, "isup", False):
            return True
    return False


def _collect_disks(paths: list[str]) -> list[dict[str, Any]]:
    """Per-mount disk usage; unavailable paths are logged and skipped, not fatal."""
    disks: list[dict[str, Any]] = []
    for path in paths:
        try:
            usage = psutil.disk_usage(path)
        except OSError as exc:
            _log.warning("disk path %s unavailable, skipping: %s", path, exc)
            continue
        disks.append(
            {
                "path": path,
                "totalBytes": int(usage.total),
                "usedBytes": int(usage.used),
                "usagePercent": round(usage.percent, 1),
            }
        )
    return disks


def collect_health(disk_paths: list[str], *, cpu_sample_seconds: float = 1.0) -> dict[str, Any]:
    """Collect the full ``health`` block.

    ``cpu_sample_seconds`` is the blocking window psutil samples CPU over; the
    default of 1s yields an accurate reading and is negligible against a 60s
    collection interval.
    """
    vm = psutil.virtual_memory()
    net = psutil.net_io_counters()
    boot = psutil.boot_time()
    cpu = psutil.cpu_percent(interval=cpu_sample_seconds)

    return {
        "cpuUsagePercent": round(cpu, 1),
        "memory": {
            "totalBytes": int(vm.total),
            "usedBytes": int(vm.used),
            "usagePercent": round(vm.percent, 1),
        },
        "disk": _collect_disks(disk_paths),
        "network": {
            "bytesSent": int(net.bytes_sent),
            "bytesRecv": int(net.bytes_recv),
            "isReachable": is_network_up(),
        },
        "uptimeSeconds": int(time.time() - boot),
        "lastBootTime": iso_utc(boot),
    }
