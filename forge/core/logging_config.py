"""Centralized logging configuration for all Forge modules."""

from __future__ import annotations

import io
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s %(name)-25s %(levelname)-7s %(message)s"

_SUPPRESSED_LOGGERS = ["httpx", "urllib3", "sqlalchemy.engine", "httpcore"]

# When True, Rich Console output is suppressed (TUI owns the terminal).
_TUI_MODE: bool = False


def is_tui_mode() -> bool:
    """Return True if running inside the Textual TUI."""
    return _TUI_MODE


class _LazyConsole:
    """Lazy proxy for Rich Console that checks TUI mode on each call.

    Module-level ``console = make_console()`` runs at import time, before
    the TUI has set ``_TUI_MODE``.  This proxy defers the decision so that
    once ``configure_tui_logging()`` flips the flag, all subsequent
    ``console.print()`` calls are silenced.
    """

    def __init__(self) -> None:
        self._cli_console = None
        self._null_console = None

    def _get(self):
        from rich.console import Console

        if _TUI_MODE:
            if self._null_console is None:
                self._null_console = Console(file=io.StringIO(), quiet=True)
            return self._null_console
        if self._cli_console is None:
            self._cli_console = Console(stderr=True)
        return self._cli_console

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


def make_console():
    """Create a lazy Rich Console proxy.

    In TUI mode (after ``configure_tui_logging()``), all output is
    suppressed so Rich prints never corrupt the Textual display.
    In CLI mode, writes to stderr.
    """
    return _LazyConsole()


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
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=3)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Suppress noisy third-party loggers.
    for name in _SUPPRESSED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_tui_logging() -> None:
    """Configure logging for TUI mode.

    Redirects all forge logging to a file (`.forge/forge.log`) instead of
    stderr, and sets the global _TUI_MODE flag so Rich Console output is
    suppressed.
    """
    global _TUI_MODE
    _TUI_MODE = True

    logger = logging.getLogger("forge")

    # Remove any existing stderr handlers
    logger.handlers = [
        h for h in logger.handlers
        if not (isinstance(h, logging.StreamHandler) and h.stream is sys.stderr)
    ]

    # Add file handler to .forge/forge.log
    log_dir = os.path.join(os.getcwd(), ".forge")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "forge.log")

    formatter = logging.Formatter(_LOG_FORMAT)
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=3)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Keep the level that configure_logging() already set (INFO or DEBUG
    # depending on --verbose).  Don't force DEBUG — that leaks noisy SDK
    # messages like "Skipping unknown SDK message type: rate_limit_event".

    # Suppress noisy third-party loggers
    for name in _SUPPRESSED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
