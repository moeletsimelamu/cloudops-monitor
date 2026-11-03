"""The log explorer's query endpoint."""

from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from cloudops.api.deps import OperatorDep, SessionDep
from cloudops.api.params import Cursor, Limit, TimeRange, before_cursor, encode_cursor, time_range
from cloudops.db.models import Application, LogEntry
from cloudops.errors import AppError, NotFoundError
from cloudops.ingest.logs import SEVERITY_LEVELS, SEVERITY_NAMES, SeverityName

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[OperatorDep])

MAX_LOG_SPAN = timedelta(days=7)


class LogOut(BaseModel):
    id: int
    timestamp: datetime
    received_at: datetime
    severity: str
    event: str | None
    message: str
    correlation_id: str | None
    environment: str | None
    application_slug: str
    application_name: str
    attributes: dict[str, Any] | None


class LogPage(BaseModel):
    items: list[LogOut]
    next_cursor: str | None


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=LogPage)
def query_logs(
    db: SessionDep,
    window: Annotated[TimeRange, Depends(time_range)],
    application: Annotated[str | None, Query(max_length=40)] = None,
    min_severity: Annotated[SeverityName | None, Query()] = None,
    event: Annotated[str | None, Query(max_length=100)] = None,
    correlation_id: Annotated[str | None, Query(max_length=100)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Text in the message.")] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> LogPage:
    """Log entries in the time range (at most 7 days), newest first."""
    if window.end - window.start > MAX_LOG_SPAN:
        raise AppError(
            "Logs can be queried over at most 7 days at a time. Narrow 'from' or 'to'.",
            code="invalid_time_range",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    query = (
        select(LogEntry, Application.slug, Application.name)
        .join(Application, LogEntry.application_id == Application.id)
        .where(LogEntry.timestamp >= window.start, LogEntry.timestamp <= window.end)
    )
    if application:
        app_id = db.scalar(select(Application.id).where(Application.slug == application))
        if app_id is None:
            raise NotFoundError(f"There is no application '{application}'.")
        query = query.where(LogEntry.application_id == app_id)
    if min_severity:
        query = query.where(LogEntry.severity >= SEVERITY_LEVELS[min_severity])
    if event:
        query = query.where(LogEntry.event == event)
    if correlation_id:
        query = query.where(LogEntry.correlation_id == correlation_id)
    if q:
        query = query.where(LogEntry.message.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if cursor:
        query = query.where(before_cursor(cursor, LogEntry.timestamp, LogEntry.id))
    rows = db.execute(
        query.order_by(LogEntry.timestamp.desc(), LogEntry.id.desc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    items = [
        LogOut(
            id=entry.id,
            timestamp=entry.timestamp,
            received_at=entry.received_at,
            severity=SEVERITY_NAMES.get(entry.severity, str(entry.severity)),
            event=entry.event,
            message=entry.message,
            correlation_id=entry.correlation_id,
            environment=entry.environment,
            application_slug=slug,
            application_name=name,
            attributes=entry.attributes,
        )
        for entry, slug, name in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(last.timestamp, last.id)
    return LogPage(items=items, next_cursor=next_cursor)
