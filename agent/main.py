"""Agent entrypoint and collection loop (AGENT_SPEC.md §4.3, §8).

Ties the pieces together: every ``intervalSeconds`` it collects health, runs the
configured backup checker, builds one payload, and delivers it. On a successful
delivery it also drains the offline retry queue oldest-first. The loop is
resilient — an unexpected error in one cycle is logged and the loop continues —
and shuts down cleanly on SIGINT/SIGTERM (the signals systemd and Windows use to
stop a service).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

from agent import __version__
from agent.collectors.backup import run_backup_check
from agent.collectors.health import collect_health
from agent.config import AgentConfig, ConfigError, is_permission_secure, load_config
from agent.logging_setup import configure_logging, get_logger
from agent.payload import build_payload
from agent.transport.retry_queue import RetryQueue
from agent.transport.sender import Sender, SendOutcome

_log = get_logger()

_DEFAULT_CONFIG_ENV = "MONITORING_AGENT_CONFIG"
_DEFAULT_CONFIG_PATH = "config.json"


class AgentRunner:
    """Owns the collection loop and the deliver/buffer/flush policy."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        sender: Sender | None = None,
        queue: RetryQueue | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._config = config
        self._sender = sender or Sender(
            config.health_endpoint, config.token, timeout_seconds=config.timeout_seconds
        )
        self._queue = queue or RetryQueue(
            config.queue_path, retention_hours=config.retry_retention_hours
        )
        self._stop = stop_event or threading.Event()

    # ---- collection --------------------------------------------------- #
    def _collect(self) -> dict:
        health = collect_health(self._config.disk_paths)
        backup = run_backup_check(self._config.backup)
        return build_payload(self._config.server_id, health, backup)

    # ---- delivery policy ---------------------------------------------- #
    def _deliver(self, payload: dict) -> None:
        outcome = self._sender.send(payload)
        if outcome is SendOutcome.DELIVERED:
            self._flush_queue()
        elif outcome is SendOutcome.RETRY:
            self._queue.enqueue(payload)
        # DROP_AUTH / DROP_CLIENT: payload discarded (already logged); keep going.

    def _flush_queue(self) -> None:
        """Resend buffered payloads oldest-first until one must be retried."""
        budget = self._queue.count()  # bound the loop
        while budget > 0 and not self._stop.is_set():
            budget -= 1
            items = self._queue.peek(limit=1)
            if not items:
                break
            item = items[0]
            outcome = self._sender.send(item.payload)
            if outcome is SendOutcome.RETRY:
                break  # backend still unhappy; leave the rest buffered
            # delivered, or permanently rejected -> remove and continue draining
            self._queue.delete(item.id)

    def run_cycle(self) -> None:
        payload = self._collect()
        self._deliver(payload)

    # ---- lifecycle ---------------------------------------------------- #
    def install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            _log.info("received signal %s; shutting down", signum)
            self._stop.set()

        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handler)
                except (ValueError, OSError):  # not in main thread / unsupported
                    pass

    def run_forever(self) -> None:
        _log.info(
            "agent starting: server=%s os-endpoint=%s interval=%ss",
            self._config.server_id,
            self._config.health_endpoint,
            self._config.interval_seconds,
        )
        while not self._stop.is_set():
            try:
                self.run_cycle()
            except Exception:  # noqa: BLE001 - one bad cycle must not kill the loop
                _log.exception("collection cycle failed; continuing")
            self._stop.wait(self._config.interval_seconds)
        _log.info("agent stopped")

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._queue.close()


def _default_config_path() -> str:
    return os.environ.get(_DEFAULT_CONFIG_ENV, _DEFAULT_CONFIG_PATH)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitoring-agent",
        description="Server health + backup monitoring agent.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=_default_config_path(),
        help="path to config.json (default: $%s or ./config.json)" % _DEFAULT_CONFIG_ENV,
    )
    parser.add_argument(
        "--once", action="store_true", help="run a single collection cycle and exit"
    )
    parser.add_argument(
        "--check-config", action="store_true", help="validate config and exit"
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(__version__)
        return 0

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    logger = configure_logging(config.log_file, config.log_level)
    if not is_permission_secure(args.config):
        logger.warning(
            "config file %s is readable by group/other and holds a token; "
            "run 'chmod 600 %s'",
            args.config,
            args.config,
        )

    if args.check_config:
        logger.info("configuration OK (server=%s)", config.server_id)
        return 0

    runner = AgentRunner(config)
    runner.install_signal_handlers()
    try:
        if args.once:
            runner.run_cycle()
        else:
            runner.run_forever()
    finally:
        runner.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
