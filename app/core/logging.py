# app/core/logging.py

import logging

import structlog

from app.core.config import settings


def setup_logging(*, json_output: bool = False) -> None:
    """Configure structlog and stdlib logging."""

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )
    exception_processor = (
        structlog.processors.dict_tracebacks
        if json_output
        else structlog.dev.set_exc_info
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            exception_processor,
            structlog.processors.TimeStamper(fmt="iso" if json_output else "%Y-%m-%d %H:%M:%S"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
