"""Fleet-wide views: incidents across applications, and the overview dashboard's data.

Everything here is derived from stored records. Nothing is estimated or filled in: an application
with too little data says so.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from cloudops.api.application_schemas import (
    ApplicationOut,
    AvailabilityOut,
    IncidentOut,
)
from cloudops.api.deployments import DeploymentOut, deployment_out
from cloudops.api.deps import OperatorDep, SessionDep
from cloudops.api.events import EventOut, event_out
from cloudops.api.params import Cursor, Limit, before_cursor, encode_cursor
from cloudops.api.views import METRIC_DEFINITIONS, application_views, fleet_health, incident_views
from cloudops.db.models import Application, Deployment, Incident, OperationalEvent
from cloudops.db.types import utc_now
from cloudops.errors import NotFoundError

router = APIRouter(tags=["overview"], dependencies=[OperatorDep])

HEALTH_WINDOW = timedelta(hours=24)
# Worst first: this is the order problems are listed in.
_ATTENTION_ORDER = {"unavailable": 0, "degraded": 1, "stale": 2}


class ApplicationIncidentOut(IncidentOut):
    application_slug: str
    application_name: str
    environment: str


class IncidentPage(BaseModel):
    items: list[ApplicationIncidentOut]
    next_cursor: str | None


def _incidents_with_applications(
    db: Session, rows: list[tuple[Incident, Application]]
) -> list[ApplicationIncidentOut]:
    views = incident_views(db, [incident for incident, _ in rows])
    return [
        ApplicationIncidentOut(
            **view.model_dump(),
            application_slug=app.slug,
            application_name=app.name,
            environment=app.environment,
        )
        for view, (_, app) in zip(views, rows, strict=True)
    ]


@router.get("/incidents", response_model=IncidentPage)
def list_incidents(
    db: SessionDep,
    status: Annotated[Literal["open", "closed", "all"], Query()] = "all",
    application: Annotated[str | None, Query(max_length=40)] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> IncidentPage:
    """Incidents across every application, most recently opened first."""
    query = select(Incident, Application).join(
        Application, Incident.application_id == Application.id
    )
    if status == "open":
        query = query.where(Incident.closed_at.is_(None))
    elif status == "closed":
        query = query.where(Incident.closed_at.is_not(None))
    if application:
        app_id = db.scalar(select(Application.id).where(Application.slug == application))
        if app_id is None:
            raise NotFoundError(f"There is no application '{application}'.")
        query = query.where(Incident.application_id == app_id)
    if cursor:
        query = query.where(before_cursor(cursor, Incident.opened_at, Incident.id))
    rows = db.execute(
        query.order_by(Incident.opened_at.desc(), Incident.id.desc()).limit(limit + 1)
    ).all()
    page = [(incident, app) for incident, app in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(last.opened_at, last.id)
    return IncidentPage(items=_incidents_with_applications(db, page), next_cursor=next_cursor)


class FleetEntry(BaseModel):
    application: ApplicationOut
    availability_24h: AvailabilityOut
    p95_ms_24h: int | None
    latest_deployment: DeploymentOut | None


class StatusCounts(BaseModel):
    total: int
    healthy: int
    degraded: int
    unavailable: int
    stale: int
    pending: int
    paused: int


class OverviewOut(BaseModel):
    generated_at: datetime
    health_window_hours: int
    counts: StatusCounts
    needs_attention: list[FleetEntry]
    fleet: list[FleetEntry]
    recent_incidents: list[ApplicationIncidentOut]
    recent_deployments: list[DeploymentOut]
    recent_changes: list[EventOut]
    definitions: dict[str, str]


def _latest_deployments(db: Session, ids: list[int]) -> dict[int, Deployment]:
    newest = (
        select(
            Deployment.id,
            func.row_number()
            .over(
                partition_by=Deployment.application_id,
                order_by=(Deployment.queued_at.desc(), Deployment.id.desc()),
            )
            .label("position"),
        )
        .where(Deployment.application_id.in_(ids))
        .subquery()
    )
    rows = db.scalars(
        select(Deployment).join(newest, Deployment.id == newest.c.id).where(newest.c.position == 1)
    )
    return {deployment.application_id: deployment for deployment in rows}


def _attention_sort_key(entry: FleetEntry) -> tuple[int, datetime]:
    application = entry.application
    since = application.stale_since if application.status == "stale" else application.state_since
    return (_ATTENTION_ORDER[application.status], since or application.created_at)


@router.get("/overview", response_model=OverviewOut)
def overview(db: SessionDep) -> OverviewOut:
    """What needs attention now, every application's state, and what changed recently."""
    now = utc_now()
    apps = db.scalars(select(Application).order_by(Application.name, Application.id)).all()
    by_id = {app.id: app for app in apps}
    views = application_views(db, apps, now)
    health = fleet_health(db, apps, now - HEALTH_WINDOW, now)
    latest = _latest_deployments(db, list(by_id))

    fleet = []
    for view in views:
        availability, p95 = health[view.id]
        deployment = latest.get(view.id)
        fleet.append(
            FleetEntry(
                application=view,
                availability_24h=availability,
                p95_ms_24h=p95,
                latest_deployment=deployment_out(deployment, by_id[view.id])
                if deployment
                else None,
            )
        )
    statuses = [view.status for view in views]

    closed = db.execute(
        select(Incident, Application)
        .join(Application, Incident.application_id == Application.id)
        .where(Incident.closed_at.is_not(None), Incident.closed_at >= now - HEALTH_WINDOW)
        .order_by(Incident.closed_at.desc(), Incident.id.desc())
        .limit(10)
    ).all()
    deployments = db.execute(
        select(Deployment, Application)
        .join(Application, Deployment.application_id == Application.id)
        .order_by(Deployment.queued_at.desc(), Deployment.id.desc())
        .limit(10)
    ).all()
    changes = db.execute(
        select(OperationalEvent, Application.slug, Application.name)
        .outerjoin(Application, OperationalEvent.application_id == Application.id)
        .where(
            or_(
                OperationalEvent.type.startswith("health."),
                OperationalEvent.type.startswith("deployment."),
            )
        )
        .order_by(OperationalEvent.occurred_at.desc(), OperationalEvent.id.desc())
        .limit(20)
    ).all()

    return OverviewOut(
        generated_at=now,
        health_window_hours=int(HEALTH_WINDOW.total_seconds() // 3600),
        counts=StatusCounts(
            total=len(views),
            healthy=statuses.count("healthy"),
            degraded=statuses.count("degraded"),
            unavailable=statuses.count("unavailable"),
            stale=statuses.count("stale"),
            pending=statuses.count("pending"),
            paused=statuses.count("paused"),
        ),
        needs_attention=sorted(
            (e for e in fleet if e.application.status in _ATTENTION_ORDER),
            key=_attention_sort_key,
        ),
        fleet=fleet,
        recent_incidents=_incidents_with_applications(db, [(i, a) for i, a in closed]),
        recent_deployments=[deployment_out(d, a) for d, a in deployments],
        recent_changes=[event_out(e, slug, name) for e, slug, name in changes],
        definitions=METRIC_DEFINITIONS,
    )
