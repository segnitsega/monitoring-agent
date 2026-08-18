"""Agent logging: a rotating local log file plus optional stderr output.

Kept deliberately simple and dependency-free. The token and any other secret is
never passed to the logger by callers; this module only decides *where* logs go.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOGGER_NAME = "monitoring_agent"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(
    log_file: str | None,
    level: str = "INFO",
    *,
    to_stderr: bool = True,
) -> logging.Logger:
    """Configure and return the agent's root logger.

    A :class:`RotatingFileHandler` is added when ``log_file`` is set. If the log
    file cannot be opened (e.g. permissions), the agent still runs and logs to
    stderr — logging must never be the reason a monitoring agent dies.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:  # pragma: no cover - environment dependent
            to_stderr = True
            logger.addHandler(logging.StreamHandler())
            logger.warning("could not open log file %s (%s); logging to stderr", log_file, exc)

    if to_stderr or not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def get_logger() -> logging.Logger:
    """Return the shared agent logger (configure it first via :func:`configure_logging`)."""
    return logging.getLogger(LOGGER_NAME)
