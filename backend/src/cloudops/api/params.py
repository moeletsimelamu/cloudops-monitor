"""Query parameters shared by history endpoints: time ranges and keyset pagination."""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Annotated

from fastapi import Query
from pydantic import AwareDatetime
from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from cloudops.db.types import utc_now
from cloudops.errors import AppError

DEFAULT_SPAN = timedelta(hours=24)
MAX_SPAN = timedelta(days=30)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


def time_range(
    from_: Annotated[
        AwareDatetime | None,
        Query(
            alias="from",
            description="Start (ISO 8601 with a time zone). Default: 24 h before `to`.",
        ),
    ] = None,
    to: Annotated[
        AwareDatetime | None,
        Query(description="End (ISO 8601 with a time zone). Default: now."),
    ] = None,
) -> TimeRange:
    end = to or utc_now()
    start = from_ or end - DEFAULT_SPAN
    if start >= end:
        raise AppError(
            "'from' must be earlier than 'to'.",
            code="invalid_time_range",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    if end - start > MAX_SPAN:
        raise AppError(
            "The time range can be at most 30 days. Narrow 'from' or 'to'.",
            code="invalid_time_range",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    return TimeRange(start, end)


def optional_time_range(
    from_: Annotated[
        AwareDatetime | None,
        Query(alias="from", description="Start (ISO 8601 with a time zone). Default: no limit."),
    ] = None,
    to: Annotated[
        AwareDatetime | None,
        Query(description="End (ISO 8601 with a time zone). Default: no limit."),
    ] = None,
) -> tuple[datetime | None, datetime | None]:
    """A time range that may be open at either end, for records rare enough that "the newest ones,
    however old" is the useful default. Paging bounds the result, so there is no maximum span."""
    if from_ and to and from_ >= to:
        raise AppError(
            "'from' must be earlier than 'to'.",
            code="invalid_time_range",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    return from_, to


Limit = Annotated[int, Query(ge=1, le=200, description="Page size (1-200).")]
Before = Annotated[
    int | None, Query(ge=1, description="Return items older than this ID (from next_before).")
]
Cursor = Annotated[
    str | None,
    Query(max_length=200, description="Continue from this position (from next_cursor)."),
]


def encode_cursor(moment: datetime, row_id: int) -> str:
    """An opaque keyset position. Rows are ordered by time and then ID, because time does not
    follow ID order: logs and backfilled deployments can arrive late."""
    return base64.urlsafe_b64encode(f"{moment.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        moment, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        parsed = datetime.fromisoformat(moment)
        if parsed.tzinfo is None:
            raise ValueError("cursor without a time zone")
        return parsed, int(row_id)
    except ValueError:  # includes binascii.Error and UnicodeDecodeError
        raise AppError(
            "The cursor is not valid. Start again from the first page.",
            code="invalid_cursor",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from None


def before_cursor(
    cursor: str,
    time_column: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[int],
) -> ColumnElement[bool]:
    """Rows older than the cursor, for queries ordered by time and ID, newest first."""
    moment, row_id = decode_cursor(cursor)
    return or_(time_column < moment, and_(time_column == moment, id_column < row_id))
