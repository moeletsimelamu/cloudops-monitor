"""Query parameters shared by history endpoints: time ranges and keyset pagination."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Annotated

from fastapi import Query
from pydantic import AwareDatetime

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


Limit = Annotated[int, Query(ge=1, le=200, description="Page size (1-200).")]
Before = Annotated[
    int | None, Query(ge=1, description="Return items older than this ID (from next_before).")
]
