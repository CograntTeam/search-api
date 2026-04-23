"""Structured JSON logging setup.

One line per log record, JSON-encoded to stdout. Every record carries the
active request ID when one is in flight, sourced from a contextvar so it
survives background tasks and doesn't need explicit plumbing.
"""

import logging
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

from app.middleware import current_request_id


class CograntJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that adds ``request_id`` when available."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        rid = current_request_id.get()
        if rid:
            log_record["request_id"] = rid


def configure_logging(level: str = "INFO") -> None:
    """Replace the root handler with a JSON formatter writing to stdout.

    Render (and most PaaS) capture stdout, so this is the simplest correct setup.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = CograntJsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Tone down the noisy libraries.
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")
