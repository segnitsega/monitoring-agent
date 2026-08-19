"""Small time helpers for producing ISO-8601 UTC timestamps.

Centralised so every timestamp the agent emits uses the exact same format
(``YYYY-MM-DDTHH:MM:SSZ``) required by the data contract in AGENT_SPEC.md §6.
"""

from __future__ import annotations

from datetime import datetime, timezone

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def iso_utc(epoch: float) -> str:
    """Format a Unix epoch as an ISO-8601 UTC string (second precision)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(_FORMAT)


def now_iso() -> str:
    """Current time as an ISO-8601 UTC string."""
    return datetime.now(timezone.utc).strftime(_FORMAT)
