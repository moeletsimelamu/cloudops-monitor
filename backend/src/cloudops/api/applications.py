"""The application registry, its monitoring controls and its health history.

Every route needs the operator: configuration and operational history are not public.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from http import HTTPStatus
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from cloudops.api.application_schemas import (
    ApplicationCreate,
    ApplicationList,
    ApplicationOut,
    ApplicationUpdate,
    CheckOut,
    CheckPage,
    IncidentList,
    IngestKeyOut,
    MetricsOut,
    MonitoringSettings,
    RequestMetricsOut,
)
from cloudops.api.deps import OperatorDep, SessionDep
from cloudops.api.params import Before, Limit, TimeRange, time_range
from cloudops.api.views import (
    application_views,
    check_out,
    incident_views,
    metrics,
    request_metrics,
)
from cloudops.auth import hash_secret
from cloudops.db.models import Application, HealthCheck, Incident, SystemState
from cloudops.db.session import run_db_work, session_scope
from cloudops.db.types import utc_now
from cloudops.deployments.sync import sync_state_key
from cloudops.errors import AppError, ConflictError, NotFoundError
from cloudops.events import record_event
from cloudops.ingest.logs import generate_ingest_key
from cloudops.monitoring.probe import system_resolver
from cloudops.monitoring.runner import CheckAlreadyRunningError, CheckRunner
from cloudops.monitoring.target_policy import TargetPolicy, TargetPolicyError

router = APIRouter(prefix="/applications", tags=["applications"], dependencies=[OperatorDep])

MANUAL_CHECK_INTERVAL_SECONDS = 5.0
REGISTRATION_DNS_TIMEOUT_SECONDS = 3.0
_resolver_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cloudops-dns")


# --- helpers ----------------------------------------------------------------------------------


def _target_error(message: str) -> AppError:
    return AppError(
        message,
        code="target_not_allowed",
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        details=[{"location": ["body", "check_url"], "message": message}],
    )


def _resolve(host: str, port: int) -> list[str]:
    async def lookup() -> list[str]:
        return await system_resolver(host, port)

    return anyio.run(lookup)


def _check_target(policy: TargetPolicy, url: str) -> None:
    """Apply the target policy; in public mode also check where the host name points now."""
    try:
        target = policy.check(url)
    except TargetPolicyError as error:
        raise _target_error(str(error)) from None
    if policy.mode != "public" or policy.is_listed(target) or target.is_ip_literal:
        return
    future = _resolver_pool.submit(_resolve, target.host, target.port)
    try:
        addresses = future.result(timeout=REGISTRATION_DNS_TIMEOUT_SECONDS)
    except FutureTimeout:
        raise _target_error(f"Resolving {target.host} took too long; try again.") from None
    except OSError:
        raise _target_error(f"{target.host} could not be resolved.") from None
    for address in addresses:
        reason = policy.blocked_reason(address, listed=False)
        if reason:
            raise _target_error(f"{target.host} resolves to {address}, {reason}.")


def _policy(request: Request) -> TargetPolicy:
    runner: CheckRunner = request.app.state.check_runner
    return runner.policy


def _get(db: Session, slug: str) -> Application:
    app = db.scalars(select(Application).where(Application.slug == slug)).one_or_none()
    if app is None:
        raise NotFoundError(f"There is no application '{slug}'.")
    return app


def _view(db: Session, app: Application) -> ApplicationOut:
    return application_views(db, [app], utc_now())[0]


# --- registry ---------------------------------------------------------------------------------


@router.get("", response_model=ApplicationList)
def list_applications(
    db: SessionDep,
    environment: Annotated[str | None, Query(max_length=20)] = None,
) -> ApplicationList:
    query = select(Application).order_by(Application.name, Application.id)
    if environment:
        query = query.where(Application.environment == environment)
    now = utc_now()
    return ApplicationList(
        items=application_views(db, db.scalars(query).all(), now), generated_at=now
    )


@router.post("", response_model=ApplicationOut, status_code=HTTPStatus.CREATED)
def register_application(
    body: ApplicationCreate, request: Request, db: SessionDep
) -> ApplicationOut:
    if db.scalars(select(Application.id).where(Application.slug == body.slug)).first():
        raise ConflictError(f"An application with the slug '{body.slug}' already exists.")
    _check_target(_policy(request), body.check_url)
    now = utc_now()
    app = Application(**body.model_dump(), next_check_at=now, created_at=now, updated_at=now)
    db.add(app)
    db.flush()
    record_event(
        db,
        type="application.registered",
        severity="info",
        summary=f"{app.name} registered for monitoring ({app.environment})",
        occurred_at=now,
        application_id=app.id,
        details={"slug": app.slug, "environment": app.environment},
    )
    if not app.monitoring_enabled:
        record_event(
            db,
            type="monitoring.paused",
            severity="info",
            summary=f"{app.name}: registered with monitoring paused",
            occurred_at=now,
            application_id=app.id,
        )
    return _view(db, app)


@router.get("/{slug}", response_model=ApplicationOut)
def get_application(slug: str, db: SessionDep) -> ApplicationOut:
    return _view(db, _get(db, slug))


@router.patch("/{slug}", response_model=ApplicationOut)
def update_application(
    slug: str, body: ApplicationUpdate, request: Request, db: SessionDep
) -> ApplicationOut:
    app = _get(db, slug)
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    current = {name: getattr(app, name) for name in MonitoringSettings.model_fields}
    merged = {**current, **changes}
    try:
        MonitoringSettings.model_validate(merged)
    except ValueError as error:
        raise AppError(
            "The combined settings are invalid. " + _first_message(error),
            code="invalid_request",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from None
    changed = sorted(name for name, value in changes.items() if getattr(app, name) != value)
    if not changed:
        return _view(db, app)
    if "check_url" in changed:
        _check_target(_policy(request), changes["check_url"])
    now = utc_now()
    for name in changed:
        setattr(app, name, changes[name])
    if {"check_url", "interval_seconds", "timeout_seconds"} & set(changed):
        app.next_check_at = now  # check the new configuration straight away
    app.updated_at = now
    record_event(
        db,
        type="monitoring.configured",
        severity="info",
        summary=f"{app.name}: configuration changed ({', '.join(changed)})",
        occurred_at=now,
        application_id=app.id,
        # Field names only: values (such as URLs) are visible on the application itself.
        details={"changed": changed},
    )
    return _view(db, app)


def _first_message(error: ValueError) -> str:
    errors = getattr(error, "errors", None)
    if callable(errors):
        items = errors()
        if items:
            message = str(items[0].get("msg", ""))
            return message.removeprefix("Value error, ")
    return str(error)


@router.delete("/{slug}", status_code=HTTPStatus.NO_CONTENT)
def delete_application(slug: str, db: SessionDep) -> Response:
    app = _get(db, slug)
    name, removed_slug = app.name, app.slug
    sync_state = db.get(SystemState, sync_state_key(app.id))
    if sync_state is not None:
        db.delete(sync_state)
    db.delete(app)
    db.flush()
    record_event(
        db,
        type="application.removed",
        severity="info",
        summary=f"{name} removed from monitoring; its checks, incidents and logs were deleted",
        occurred_at=utc_now(),
        details={"slug": removed_slug, "name": name},
    )
    return Response(status_code=HTTPStatus.NO_CONTENT)


def _set_monitoring(db: Session, slug: str, enabled: bool) -> ApplicationOut:
    app = _get(db, slug)
    if app.monitoring_enabled == enabled:
        return _view(db, app)
    now = utc_now()
    app.monitoring_enabled = enabled
    app.updated_at = now
    if enabled:
        app.next_check_at = now
    record_event(
        db,
        type="monitoring.resumed" if enabled else "monitoring.paused",
        severity="info",
        summary=f"{app.name}: monitoring {'resumed' if enabled else 'paused'}",
        occurred_at=now,
        application_id=app.id,
    )
    return _view(db, app)


@router.post("/{slug}/pause", response_model=ApplicationOut)
def pause_monitoring(slug: str, db: SessionDep) -> ApplicationOut:
    return _set_monitoring(db, slug, enabled=False)


@router.post("/{slug}/resume", response_model=ApplicationOut)
def resume_monitoring(slug: str, db: SessionDep) -> ApplicationOut:
    return _set_monitoring(db, slug, enabled=True)


@router.post("/{slug}/ingest-key", response_model=IngestKeyOut, status_code=HTTPStatus.CREATED)
def create_ingest_key(slug: str, db: SessionDep) -> IngestKeyOut:
    """Create or replace the key this application ships logs with. Shown once; any previous key
    stops working immediately."""
    app = _get(db, slug)
    rotated = app.ingest_key_hash is not None
    key = generate_ingest_key()
    now = utc_now()
    app.ingest_key_hash = hash_secret(key)
    app.updated_at = now
    record_event(
        db,
        type="monitoring.ingest_key_rotated" if rotated else "monitoring.ingest_key_created",
        severity="info",
        summary=f"{app.name}: log ingest key {'rotated' if rotated else 'created'}",
        occurred_at=now,
        application_id=app.id,
    )
    return IngestKeyOut(ingest_key=key, application_slug=app.slug)


@router.delete("/{slug}/ingest-key", status_code=HTTPStatus.NO_CONTENT)
def revoke_ingest_key(slug: str, db: SessionDep) -> Response:
    app = _get(db, slug)
    if app.ingest_key_hash is not None:
        now = utc_now()
        app.ingest_key_hash = None
        app.updated_at = now
        record_event(
            db,
            type="monitoring.ingest_key_revoked",
            severity="info",
            summary=f"{app.name}: log ingest key revoked",
            occurred_at=now,
            application_id=app.id,
        )
    return Response(status_code=HTTPStatus.NO_CONTENT)


# --- checks and history -----------------------------------------------------------------------


def _load_target(request: Request, slug: str) -> tuple[int, str, int]:
    with session_scope(request.app.state.session_factory) as db:
        app = _get(db, slug)
        return app.id, app.check_url, app.timeout_seconds


def _load_check(request: Request, check_id: int) -> CheckOut:
    with session_scope(request.app.state.session_factory) as db:
        check = db.get(HealthCheck, check_id)
        if check is None:
            raise NotFoundError("The check was removed before it could be returned.")
        return check_out(check)


@router.post("/{slug}/checks", response_model=CheckOut, status_code=HTTPStatus.CREATED)
async def run_check_now(slug: str, request: Request) -> CheckOut:
    """Run a health check immediately and return its result. Stored like any other check."""
    app_id, url, timeout = await run_db_work(_load_target, request, slug)
    last_manual: dict[int, float] = request.app.state.manual_checks
    wait = MANUAL_CHECK_INTERVAL_SECONDS - (time.monotonic() - last_manual.get(app_id, -1e9))
    if wait > 0:
        raise AppError(
            f"A check was run moments ago. Try again in {int(wait) + 1} seconds.",
            code="too_many_checks",
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(int(wait) + 1)},
        )
    runner: CheckRunner = request.app.state.check_runner
    try:
        runner.claim(app_id)
    except CheckAlreadyRunningError:
        raise AppError(
            "A check of this application is already running; its result will appear shortly.",
            code="check_in_progress",
            status_code=HTTPStatus.CONFLICT,
        ) from None
    last_manual[app_id] = time.monotonic()
    result = await runner.run(app_id, url, timeout, manual=True)
    if result is None:
        raise NotFoundError(f"There is no application '{slug}'.")
    return await run_db_work(_load_check, request, result.check_id)


@router.get("/{slug}/checks", response_model=CheckPage)
def list_checks(
    slug: str,
    db: SessionDep,
    window: Annotated[TimeRange, Depends(time_range)],
    limit: Limit = 50,
    before: Before = None,
    signal: Annotated[Literal["ok", "warn", "fail", "inconclusive"] | None, Query()] = None,
) -> CheckPage:
    """Checks in the time range, newest first."""
    app = _get(db, slug)
    query = select(HealthCheck).where(
        HealthCheck.application_id == app.id,
        HealthCheck.started_at >= window.start,
        HealthCheck.started_at <= window.end,
    )
    if signal:
        query = query.where(HealthCheck.signal == signal)
    if before:
        query = query.where(HealthCheck.id < before)
    rows = db.scalars(query.order_by(HealthCheck.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    return CheckPage(
        items=[check_out(c) for c in page],
        next_before=page[-1].id if len(rows) > limit else None,
    )


@router.get("/{slug}/incidents", response_model=IncidentList)
def list_incidents(slug: str, db: SessionDep, limit: Limit = 20) -> IncidentList:
    app = _get(db, slug)
    rows = db.scalars(
        select(Incident)
        .where(Incident.application_id == app.id)
        .order_by(Incident.opened_at.desc(), Incident.id.desc())
        .limit(limit)
    ).all()
    return IncidentList(items=incident_views(db, rows))


@router.get("/{slug}/metrics", response_model=MetricsOut)
def application_metrics(
    slug: str,
    db: SessionDep,
    window: Annotated[Literal["1h", "6h", "24h", "7d"], Query()] = "24h",
) -> MetricsOut:
    """Availability, coverage and response times over a window ending now."""
    return metrics(db, _get(db, slug), window, utc_now())


@router.get("/{slug}/request-metrics", response_model=RequestMetricsOut)
def application_request_metrics(
    slug: str,
    db: SessionDep,
    window: Annotated[Literal["1h", "6h", "24h", "7d"], Query()] = "24h",
) -> RequestMetricsOut:
    """Requests and 5xx responses from the application's own request logs, if it ships them."""
    return request_metrics(db, _get(db, slug), window, utc_now())
