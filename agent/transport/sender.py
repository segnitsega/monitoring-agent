"""HTTPS transport for delivering payloads to the ingestion API (AGENT_SPEC.md §4.3).

POSTs to ``/api/v1/health`` with a ``Bearer`` token and classifies the outcome
so the collection loop knows what to do with the payload:

- **2xx** -> ``DELIVERED``: done.
- **401/403** -> ``DROP_AUTH``: the per-server token is bad; retrying can't fix
  it, so the payload is dropped (agent keeps collecting, per §4.3).
- **429 / 5xx / network / timeout** -> ``RETRY``: transient; buffer and resend.
- **other 4xx** -> ``DROP_CLIENT``: malformed request; retrying won't help.

The token is only ever placed in the request header — never logged.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

import requests

from agent.logging_setup import get_logger

_log = get_logger()


class SendOutcome(Enum):
    """What the loop should do with a payload after a send attempt."""

    DELIVERED = auto()
    RETRY = auto()
    DROP_AUTH = auto()
    DROP_CLIENT = auto()


class Sender:
    """Stateless-ish HTTPS sender wrapping a :class:`requests.Session`."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout_seconds: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout_seconds
        self._session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _classify(status: int) -> SendOutcome:
        if 200 <= status < 300:
            return SendOutcome.DELIVERED
        if status in (401, 403):
            return SendOutcome.DROP_AUTH
        if status == 429 or 500 <= status < 600:
            return SendOutcome.RETRY
        if 400 <= status < 500:
            return SendOutcome.DROP_CLIENT
        return SendOutcome.RETRY  # unexpected 1xx/3xx — be conservative, retry

    def send(self, payload: dict[str, Any]) -> SendOutcome:
        """Attempt one delivery; never raises."""
        try:
            resp = self._session.post(
                self._endpoint,
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            _log.warning("delivery failed (network/timeout): %s", exc)
            return SendOutcome.RETRY

        outcome = self._classify(resp.status_code)
        if outcome is SendOutcome.DELIVERED:
            _log.info("payload delivered (HTTP %s)", resp.status_code)
        elif outcome is SendOutcome.DROP_AUTH:
            _log.error(
                "authentication failed (HTTP %s); dropping payload and continuing. "
                "Check the server token in config.json.",
                resp.status_code,
            )
        elif outcome is SendOutcome.DROP_CLIENT:
            _log.error("backend rejected payload (HTTP %s); dropping (won't retry)", resp.status_code)
        else:
            _log.warning("backend transient error (HTTP %s); will retry", resp.status_code)
        return outcome
