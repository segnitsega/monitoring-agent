"""Assemble the authoritative ingestion payload (AGENT_SPEC.md §6).

This is the single source of truth for the JSON shape POSTed to
``/api/v1/health``. The Express route and its validator on the backend are
expected to match this shape, not the other way around.
"""

from __future__ import annotations

from typing import Any

from agent.collectors.health import get_hostname, get_os
from agent.timeutils import now_iso


def build_payload(
    server_id: str,
    health: dict[str, Any],
    backup: dict[str, Any] | None,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Combine health + backup data into one contract-compliant payload.

    ``backup`` may be ``None`` when no backup checker is configured for the
    server — the field is still present in the payload, set to ``null``.
    """
    return {
        "serverId": server_id,
        "hostname": get_hostname(),
        "os": get_os(),
        "timestamp": timestamp or now_iso(),
        "health": health,
        "backup": backup,
    }
