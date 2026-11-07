"""Structured logging for CloudOps Monitor's own output.

Records are written to stdout, one JSON object per line by default (``CLOUDOPS_LOG_FORMAT=text``
gives a readable single-line format for development). Every record carries the current request ID,
and every rendered line passes through redaction: configured secrets and credential-shaped strings
are masked even if a message accidentally contains them.

Structured fields are passed with ``extra={"fields": {...}}``.
"""

import json
import logging
import sys
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from cloudops.domain.redaction import redact_attributes, redact_text

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.known_secrets = tuple(known_secrets)

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_text(record.getMessage(), self.known_secrets),
            "request_id": getattr(record, "request_id", "-"),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(redact_attributes(fields, self.known_secrets))
        if record.exc_info:
            entry["exception"] = redact_text(
                self.formatException(record.exc_info), self.known_secrets
            )
        return json.dumps(entry, default=str, ensure_ascii=False)


class RedactingTextFormatter(logging.Formatter):
    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        super().__init__(_TEXT_FORMAT)
        self.known_secrets = tuple(known_secrets)

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            line += " " + " ".join(f"{k}={v}" for k, v in fields.items())
        return redact_text(line, self.known_secrets)


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever ``sys.stdout`` is at the time of the record, not at creation time
    (test runners and some process managers replace it)."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging(level: str, log_format: str, known_secrets: Iterable[str] = ()) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in [h for h in root.handlers if getattr(h, "_cloudops", False)]:
        root.removeHandler(handler)
    handler = _StdoutHandler(sys.stdout)
    formatter: logging.Formatter = (
        JsonFormatter(known_secrets)
        if log_format == "json"
        else RedactingTextFormatter(known_secrets)
    )
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())
    handler._cloudops = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    # Uvicorn's own messages (start-up, shutdown, protocol errors) go through the same handler, so
    # a container's output is one format. Its default configuration gives them separate text
    # handlers.
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # The application writes its own access log line (path only, no query string).
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # The HTTP client logs every request URL at INFO; probes and GitHub calls are logged by
    # CloudOps itself with the fields that matter.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
