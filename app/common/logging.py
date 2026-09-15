"""Structured logging configuration and context binding."""

import logging
import sys
from typing import Any

import structlog

from app.config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Configure standard library and structlog processors."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=config.level.upper())
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if config.json_logs
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, config.level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    """Return the application structured logger."""
    return structlog.get_logger()

