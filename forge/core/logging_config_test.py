"""Tests for forge.core.logging_config."""

from __future__ import annotations

import logging
import os
import tempfile

import pytest

from forge.core.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _reset_forge_logger():
    """Remove all handlers from the forge logger before and after each test."""
    logger = logging.getLogger("forge")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    logger.handlers.clear()
    yield
    logger.handlers.clear()
    logger.handlers.extend(original_handlers)
    logger.level = original_level


class TestConfigureLogging:
    def test_sets_info_level_by_default(self):
        configure_logging()
        logger = logging.getLogger("forge")
        assert logger.level == logging.INFO

    def test_sets_debug_level(self):
        configure_logging(level="DEBUG")
        logger = logging.getLogger("forge")
        assert logger.level == logging.DEBUG

    def test_adds_stream_handler_to_stderr(self):
        configure_logging()
        logger = logging.getLogger("forge")
        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        import sys
        assert handler.stream is sys.stderr

    def test_suppresses_third_party_loggers(self):
        configure_logging()
        for name in ["httpx", "urllib3", "sqlalchemy.engine", "httpcore"]:
            assert logging.getLogger(name).level == logging.WARNING

    def test_idempotent_no_duplicate_handlers(self):
        configure_logging()
        configure_logging()
        logger = logging.getLogger("forge")
        assert len(logger.handlers) == 1

    def test_file_handler_added_when_log_file_specified(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "test.log")
            configure_logging(log_file=log_path)
            logger = logging.getLogger("forge")
            assert len(logger.handlers) == 2
            file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers) == 1

    def test_child_logger_inherits_configuration(self):
        configure_logging(level="DEBUG")
        child = logging.getLogger("forge.core.daemon")
        assert child.getEffectiveLevel() == logging.DEBUG

    def test_log_format(self):
        configure_logging()
        logger = logging.getLogger("forge")
        handler = logger.handlers[0]
        assert "%(asctime)s" in handler.formatter._fmt
        assert "%(name)-25s" in handler.formatter._fmt
        assert "%(levelname)-7s" in handler.formatter._fmt
