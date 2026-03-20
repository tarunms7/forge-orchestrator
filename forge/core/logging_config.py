"""Centralized logging configuration for all Forge modules."""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(name)-25s %(levelname)-7s %(message)s"

_SUPPRESSED_LOGGERS = ["httpx", "urllib3", "sqlalchemy.engine", "httpcore"]


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure the ``forge`` root logger so all ``forge.*`` loggers inherit.

    Args:
        level: Logging level name (e.g. ``'INFO'``, ``'DEBUG'``).
        log_file: Optional path to a file for an additional
            :class:`logging.FileHandler`.

    The function is idempotent — calling it twice will not add duplicate
    handlers.
    """
    logger = logging.getLogger("forge")

    # Idempotency: skip if already configured.
    if logger.handlers:
        return

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Suppress noisy third-party loggers.
    for name in _SUPPRESSED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
