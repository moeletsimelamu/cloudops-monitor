"""Validating and storing log entries pushed by monitored applications.

Each entry is validated on its own, so one bad entry does not lose the rest of a batch. Everything
is redacted before storage (docs/ARCHITECTURE.md §5.1). Rejections describe the problem without
repeating the input, which may be exactly what should not be stored.
"""

import secrets
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from sqlalchemy import insert
from sqlalchemy.orm import Session

from cloudops.db.models import LogEntry
from cloudops.domain.redaction import redact_attributes, redact_text

MAX_ENTRIES_PER_BATCH = 500
MAX_BODY_BYTES = 256 * 1024
MAX_MESSAGE_LENGTH = 4096
MAX_ATTRIBUTES = 32
MAX_ATTRIBUTE_KEY_LENGTH = 64
MAX_ATTRIBUTE_VALUE_LENGTH = 1024
MAX_AGE = timedelta(hours=24)
MAX_CLOCK_SKEW = timedelta(minutes=5)

SEVERITY_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
SEVERITY_NAMES = {level: name for name, level in SEVERITY_LEVELS.items()}
SeverityName = Literal["debug", "info", "warning", "error", "critical"]

INGEST_KEY_PREFIX = "cok_"


def generate_ingest_key() -> str:
    return INGEST_KEY_PREFIX + secrets.token_urlsafe(32)


class LogEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: AwareDatetime
    severity: SeverityName
    message: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_LENGTH)]
    event: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")] | None = None
    correlation_id: (
        Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")] | None
    ) = None
    environment: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,19}$")] | None = None
    attributes: dict[str, str | int | float | bool | None] | None = Field(default=None)

    @field_validator("severity", mode="before")
    @classmethod
    def _lower_case_severity(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("attributes")
    @classmethod
    def _bounded_attributes(
        cls, value: dict[str, str | int | float | bool | None] | None
    ) -> dict[str, str | int | float | bool | None] | None:
        if value is None:
            return None
        if len(value) > MAX_ATTRIBUTES:
            raise ValueError(f"At most {MAX_ATTRIBUTES} attributes are allowed.")
        for key, item in value.items():
            if not key or len(key) > MAX_ATTRIBUTE_KEY_LENGTH:
                raise ValueError(
                    f"Attribute names must be 1-{MAX_ATTRIBUTE_KEY_LENGTH} characters long."
                )
            if isinstance(item, str) and len(item) > MAX_ATTRIBUTE_VALUE_LENGTH:
                raise ValueError(
                    f"Attribute values can be at most {MAX_ATTRIBUTE_VALUE_LENGTH} characters."
                )
        return value


@dataclass(frozen=True)
class RejectedEntry:
    index: int
    errors: list[dict[str, Any]]


def validate_entries(
    entries: Sequence[Any], now: datetime
) -> tuple[list[LogEntryIn], list[RejectedEntry]]:
    valid: list[LogEntryIn] = []
    rejected: list[RejectedEntry] = []
    for index, raw in enumerate(entries):
        try:
            entry = LogEntryIn.model_validate(raw)
        except ValidationError as error:
            rejected.append(
                RejectedEntry(
                    index,
                    [
                        {"location": [str(p) for p in item["loc"]], "message": item["msg"]}
                        for item in error.errors(include_input=False, include_url=False)
                    ],
                )
            )
            continue
        if entry.timestamp < now - MAX_AGE or entry.timestamp > now + MAX_CLOCK_SKEW:
            rejected.append(
                RejectedEntry(
                    index,
                    [
                        {
                            "location": ["timestamp"],
                            "message": (
                                "The timestamp must be within the last 24 hours and no more "
                                "than 5 minutes in the future. Check the sender's clock."
                            ),
                        }
                    ],
                )
            )
            continue
        valid.append(entry)
    return valid, rejected


def store_entries(
    db: Session,
    application_id: int,
    default_environment: str,
    entries: Iterable[LogEntryIn],
    known_secrets: Iterable[str],
    now: datetime,
) -> int:
    secrets_to_mask = tuple(known_secrets)
    rows = [
        {
            "application_id": application_id,
            "timestamp": entry.timestamp,
            "received_at": now,
            "severity": SEVERITY_LEVELS[entry.severity],
            "event": entry.event,
            # Masking can lengthen text, so the column limit is applied afterwards.
            "message": redact_text(entry.message, secrets_to_mask)[:MAX_MESSAGE_LENGTH],
            "correlation_id": entry.correlation_id,
            "environment": entry.environment or default_environment,
            "attributes": (
                redact_attributes(entry.attributes, secrets_to_mask) if entry.attributes else None
            ),
        }
        for entry in entries
    ]
    if rows:
        db.execute(insert(LogEntry), rows)
    return len(rows)


class IngestRateLimiter:
    """At most ``max_per_minute`` entries per application, in fixed one-minute windows.

    Retention caps stored rows only once an hour, so this is what stops one noisy or misbehaving
    sender from filling the disk in between. In memory and per process, like the other limiters.
    """

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._windows: dict[int, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def retry_after(self, application_id: int, entries: int) -> int:
        """Count ``entries`` if they fit; otherwise the seconds until the next window."""
        now = time.time()
        window = int(now // 60)
        with self._lock:
            current, used = self._windows.get(application_id, (window, 0))
            if current != window:
                used = 0
            if used + entries > self.max_per_minute:
                return max(1, int(60 - now % 60) + 1)
            self._windows[application_id] = (window, used + entries)
            return 0
