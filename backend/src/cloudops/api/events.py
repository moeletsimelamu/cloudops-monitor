"""The operational event timeline."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select

from cloudops.api.deps import OperatorDep, SessionDep
from cloudops.api.params import Cursor, Limit, TimeRange, before_cursor, encode_cursor, time_range
from cloudops.db.models import EVENT_SEVERITIES, Application, OperationalEvent
from cloudops.errors import NotFoundError

router = APIRouter(prefix="/events", tags=["events"], dependencies=[OperatorDep])

Category = Literal["health", "deployment", "monitoring", "application"]
SeverityName = Literal["info", "warning", "error", "critical"]

# Each category is a set of event type prefixes.
_CATEGORY_PREFIXES: dict[str, tuple[str, ...]] = {
    "health": ("health.",),
    "deployment": ("deployment.", "deployments."),
    "monitoring": ("monitoring.",),
    "application": ("application.",),
}


class EventOut(BaseModel):
    id: int
    occurred_at: datetime
    type: str
    severity: SeverityName
    application_slug: str | None
    application_name: str | None
    incident_id: int | None
    deployment_id: int | None
    summary: str
    details: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None


def event_out(event: OperationalEvent, slug: str | None, name: str | None) -> EventOut:
    return EventOut(
        id=event.id,
        occurred_at=event.occurred_at,
        type=event.type,
        severity=event.severity,
        application_slug=slug,
        application_name=name,
        incident_id=event.incident_id,
        deployment_id=event.deployment_id,
        summary=event.summary,
        details=event.details,
    )


@router.get("", response_model=EventPage)
def list_events(
    db: SessionDep,
    window: Annotated[TimeRange, Depends(time_range)],
    application: Annotated[str | None, Query(max_length=40)] = None,
    category: Annotated[Category | None, Query()] = None,
    event_type: Annotated[str | None, Query(alias="type", max_length=48)] = None,
    min_severity: Annotated[SeverityName | None, Query()] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> EventPage:
    """Events in the time range, newest first."""
    query = (
        select(OperationalEvent, Application.slug, Application.name)
        .outerjoin(Application, OperationalEvent.application_id == Application.id)
        .where(
            OperationalEvent.occurred_at >= window.start,
            OperationalEvent.occurred_at <= window.end,
        )
    )
    if application:
        app_id = db.scalar(select(Application.id).where(Application.slug == application))
        if app_id is None:
            raise NotFoundError(f"There is no application '{application}'.")
        query = query.where(OperationalEvent.application_id == app_id)
    if category:
        query = query.where(
            or_(*(OperationalEvent.type.startswith(p) for p in _CATEGORY_PREFIXES[category]))
        )
    if event_type:
        query = query.where(OperationalEvent.type == event_type)
    if min_severity:
        allowed = EVENT_SEVERITIES[EVENT_SEVERITIES.index(min_severity) :]
        query = query.where(OperationalEvent.severity.in_(allowed))
    if cursor:
        query = query.where(
            before_cursor(cursor, OperationalEvent.occurred_at, OperationalEvent.id)
        )
    rows = db.execute(
        query.order_by(OperationalEvent.occurred_at.desc(), OperationalEvent.id.desc()).limit(
            limit + 1
        )
    ).all()
    page = rows[:limit]
    items = [event_out(event, slug, name) for event, slug, name in page]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(last.occurred_at, last.id)
    return EventPage(items=items, next_cursor=next_cursor)
