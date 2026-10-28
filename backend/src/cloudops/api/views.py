"""Read models for the API: stored rows plus what is derived when they are read (freshness,
availability). Batched, so a list of applications costs a fixed number of queries."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cloudops.api.application_schemas import (
    ApplicationOut,
    AvailabilityOut,
    BucketOut,
    CheckOut,
    IncidentOut,
    MetricsOut,
)
from cloudops.db.models import Application, HealthCheck, Incident, OperationalEvent
from cloudops.domain.availability import (
    CheckSample,
    availability,
    bucket_size,
    buckets,
    monitored_seconds,
    percentile,
)
from cloudops.domain.classification import CONCLUSIVE_SIGNALS
from cloudops.domain.health_state import display_status

WINDOWS = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}

METRIC_DEFINITIONS = {
    "availability": (
        "Share of conclusive health checks in the window that did not fail (ok or warn). "
        "Checks are samples taken every interval from one location, so outages shorter than the "
        "interval can be missed. This is health-check availability, not uptime."
    ),
    "coverage": (
        "Conclusive checks divided by the checks expected while the application was monitored "
        "(not paused) in the window, using the current interval. No availability is given below "
        "50% coverage or with fewer than 10 checks."
    ),
    "latency": (
        "Nearest-rank p50 and p95 of the time until response headers arrived, for checks that got "
        "an HTTP response."
    ),
}


def check_out(check: HealthCheck) -> CheckOut:
    return CheckOut(
        id=check.id,
        started_at=check.started_at,
        duration_ms=check.duration_ms,
        http_status=check.http_status,
        signal=check.signal,
        reason=check.reason,
        detail=check.detail,
        resolved_ip=check.resolved_ip,
        state_after=check.state_after,
    )


def incident_out(incident: Incident) -> IncidentOut:
    duration = (
        int((incident.closed_at - incident.opened_at).total_seconds())
        if incident.closed_at
        else None
    )
    return IncidentOut(
        id=incident.id,
        opened_at=incident.opened_at,
        closed_at=incident.closed_at,
        worst_state=incident.worst_state,
        opening_reason=incident.opening_reason,
        duration_seconds=duration,
    )


def application_views(
    db: Session, apps: Sequence[Application], now: datetime
) -> list[ApplicationOut]:
    ids = [app.id for app in apps]
    if not ids:
        return []
    open_incidents = {
        incident.application_id: incident
        for incident in db.scalars(
            select(Incident).where(Incident.application_id.in_(ids), Incident.closed_at.is_(None))
        )
    }
    latest_ids = (
        select(func.max(HealthCheck.id))
        .where(HealthCheck.application_id.in_(ids))
        .group_by(HealthCheck.application_id)
    )
    latest_checks = {
        check.application_id: check
        for check in db.scalars(select(HealthCheck).where(HealthCheck.id.in_(latest_ids)))
    }
    return [
        _application_out(app, open_incidents.get(app.id), latest_checks.get(app.id), now)
        for app in apps
    ]


def _application_out(
    app: Application, incident: Incident | None, latest: HealthCheck | None, now: datetime
) -> ApplicationOut:
    freshness = display_status(
        state=app.health_state,  # type: ignore[arg-type]
        monitoring_enabled=app.monitoring_enabled,
        last_observed_at=app.last_observed_at,
        interval_seconds=app.interval_seconds,
        now=now,
    )
    return ApplicationOut(
        id=app.id,
        slug=app.slug,
        name=app.name,
        description=app.description,
        environment=app.environment,
        check_url=app.check_url,
        interval_seconds=app.interval_seconds,
        timeout_seconds=app.timeout_seconds,
        slow_threshold_ms=app.slow_threshold_ms,
        failure_threshold=app.failure_threshold,
        recovery_threshold=app.recovery_threshold,
        monitoring_enabled=app.monitoring_enabled,
        health_state=app.health_state,
        status=freshness.status,
        stale_since=freshness.stale_since,
        state_since=app.state_since,
        last_observed_at=app.last_observed_at,
        last_attempt_at=app.last_attempt_at,
        last_success_at=app.last_success_at,
        last_failure_at=app.last_failure_at,
        last_failure_reason=app.last_failure_reason,
        next_check_at=app.next_check_at if app.monitoring_enabled else None,
        github_repo=app.github_repo,
        deploy_workflow=app.deploy_workflow,
        deploy_branch=app.deploy_branch,
        has_ingest_key=app.ingest_key_hash is not None,
        open_incident=incident_out(incident) if incident else None,
        latest_check=check_out(latest) if latest else None,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


def _pause_history(db: Session, app_id: int, end: datetime) -> list[tuple[datetime, bool]]:
    rows = db.execute(
        select(OperationalEvent.occurred_at, OperationalEvent.type)
        .where(
            OperationalEvent.application_id == app_id,
            OperationalEvent.type.in_(("monitoring.paused", "monitoring.resumed")),
            OperationalEvent.occurred_at <= end,
        )
        .order_by(OperationalEvent.occurred_at, OperationalEvent.id)
    ).all()
    return [(row.occurred_at, row.type == "monitoring.paused") for row in rows]


def metrics(db: Session, app: Application, window: str, now: datetime) -> MetricsOut:
    span = WINDOWS[window]
    start = now - span
    rows = db.execute(
        select(
            HealthCheck.started_at,
            HealthCheck.signal,
            HealthCheck.duration_ms,
            HealthCheck.http_status,
        )
        .where(
            HealthCheck.application_id == app.id,
            HealthCheck.started_at >= start,
            HealthCheck.started_at < now,
            HealthCheck.signal.in_(CONCLUSIVE_SIGNALS),
        )
        .order_by(HealthCheck.started_at)
    ).all()
    samples = [
        CheckSample(r.started_at, r.signal, r.duration_ms, responded=r.http_status is not None)
        for r in rows
    ]
    changes = _pause_history(db, app.id, now)
    paused_before = False
    for changed_at, paused in changes:
        if changed_at <= start:
            paused_before = paused
    if not changes:
        # No pause history: the current setting has applied since the application was created.
        paused_before = not app.monitoring_enabled
    monitored = monitored_seconds(start, now, app.created_at, changes, paused_before)
    result = availability(samples, app.interval_seconds, monitored)
    size = bucket_size(span)
    durations = [s.duration_ms for s in samples if s.responded and s.duration_ms is not None]
    return MetricsOut(
        window=window,
        window_start=start,
        window_end=now,
        bucket_seconds=int(size.total_seconds()),
        availability=AvailabilityOut(
            checks=result.checks,
            ok=result.ok,
            warn=result.warn,
            fail=result.fail,
            expected_checks=result.expected_checks,
            coverage=result.coverage,
            availability=result.availability,
            insufficient_reason=result.insufficient_reason,
        ),
        p50_ms=percentile(durations, 0.5),
        p95_ms=percentile(durations, 0.95),
        buckets=[
            BucketOut(
                start=b.start, checks=b.checks, failed=b.failed, p50_ms=b.p50_ms, p95_ms=b.p95_ms
            )
            for b in buckets(samples, start, now, size)
        ],
        definitions=METRIC_DEFINITIONS,
    )
