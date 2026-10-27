"""Column types shared by the models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp stored as UTC and always returned timezone-aware.

    SQLite has no timezone-aware type, so values are stored as naive UTC. Naive input is rejected
    rather than guessed at: every timestamp in CloudOps Monitor is created as UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime given; CloudOps Monitor stores UTC timestamps only.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"Expected a datetime from the database, got {type(value).__name__}.")
        return value.replace(tzinfo=UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)
