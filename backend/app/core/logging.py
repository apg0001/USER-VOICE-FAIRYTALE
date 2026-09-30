import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

SENSITIVE_PARTS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "input_text",
    "audio",
    "storage_key",
    "external_id",
}


def redact_sensitive(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Recursively redact known credentials and voice/text payload fields."""

    def redact(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if any(part in key.lower() for part in SENSITIVE_PARTS)
                else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        if isinstance(value, bytes):
            return "[BINARY REDACTED]"
        return value

    return {
        key: "[REDACTED]"
        if any(part in key.lower() for part in SENSITIVE_PARTS)
        else redact(value)
        for key, value in event_dict.items()
    }


def configure_logging(level: str) -> None:
    """Configure JSON logs without adding secrets or raw media data."""

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

