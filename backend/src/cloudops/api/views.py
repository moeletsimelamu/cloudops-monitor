"""Read models for the API: stored rows plus what is derived when they are read (freshness,
availability). Batched, so a list of applications costs a fixed number of queries."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from cloudops.api.application_schemas import (
    ApplicationOut,
    AvailabilityOut,
    BucketOut,
    CheckOut,
    IncidentOut,
    MetricsOut,
    PrecedingDeploymentOut,
    RequestBucketOut,
    RequestMetricsOut,
)
from cloudops.db.models import (
    Application,
    Deployment,
    HealthCheck,
    Incident,
    LogEntry,
    OperationalEvent,
)
from cloudops.domain.availability import (
    Availability,
    CheckSample,
    availability,
    bucket_size,
    buckets,
    monitored_seconds,
    percentile,
)
from cloudops.domain.classification import CONCLUSIVE_SIGNALS
from cloudops.domain.correlation import (
    CORRELATION_WINDOW,
    Nearby,
    find_preceding_deployment,
)
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


def _preceding_out(nearby: Nearby[Deployment]) -> PrecedingDeploymentOut:
    deployment = nearby.deployment
    number = f"#{deployment.run_number} " if deployment.run_number else ""
    name = f"deployment {number}({deployment.commit_sha[:7]})"
    minutes = f"{nearby.minutes_before} min"
    summary = (
        f"Incident opened {minutes} after {name} started, while it was still running"
        if nearby.relation == "in_progress"
        else f"Incident opened {minutes} after {name} finished ({deployment.outcome})"
    )
    return PrecedingDeploymentOut(
        id=deployment.id,
        run_number=deployment.run_number,
        commit_sha=deployment.commit_sha,
        outcome=deployment.outcome,
        url=deployment.url,
        relation=nearby.relation,
        minutes_before=nearby.minutes_before,
        summary=summary,
    )


def incident_views(db: Session, incidents: Sequence[Incident]) -> list[IncidentOut]:
    """Incidents with the deployment that came just before each, fetched in one query."""
    if not incidents:
        return []
    earliest = min(i.opened_at for i in incidents) - CORRELATION_WINDOW * 2
    latest = max(i.opened_at for i in incidents)
    deployments: dict[int, list[Deployment]] = defaultdict(list)
    for deployment in db.scalars(
        select(Deployment).where(
            Deployment.application_id.in_({i.application_id for i in incidents}),
            Deployment.started_at >= earliest,
            Deployment.started_at <= latest,
        )
    ):
        deployments[deployment.application_id].append(deployment)
    views = []
    for incident in incidents:
        duration = (
            int((incident.closed_at - incident.opened_at).total_seconds())
            if incident.closed_at
            else None
        )
        nearby = find_preceding_deployment(incident.opened_at, deployments[incident.application_id])
        views.append(
            IncidentOut(
                id=incident.id,
                opened_at=incident.opened_at,
                closed_at=incident.closed_at,
                worst_state=incident.worst_state,
                opening_reason=incident.opening_reason,
                duration_seconds=duration,
                preceding_deployment=_preceding_out(nearby) if nearby else None,
            )
        )
    return views


def application_views(
    db: Session, apps: Sequence[Application], now: datetime
) -> list[ApplicationOut]:
    ids = [app.id for app in apps]
    if not ids:
        return []
    incidents = db.scalars(
        select(Incident).where(Incident.application_id.in_(ids), Incident.closed_at.is_(None))
    ).all()
    open_incidents = {
        incident.application_id: view
        for incident, view in zip(incidents, incident_views(db, incidents), strict=True)
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
    app: Application, incident: IncidentOut | None, latest: HealthCheck | None, now: datetime
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
        open_incident=incident,
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


def _monitored_seconds(
    app: Application,
    changes: Sequence[tuple[datetime, bool]],
    start: datetime,
    end: datetime,
) -> float:
    paused_before = False
    for changed_at, paused in changes:
        if changed_at <= start:
            paused_before = paused
    if not changes:
        # No pause history: the current setting has applied since the application was created.
        paused_before = not app.monitoring_enabled
    return monitored_seconds(start, end, app.created_at, changes, paused_before)


def availability_out(result: Availability) -> AvailabilityOut:
    return AvailabilityOut(
        checks=result.checks,
        ok=result.ok,
        warn=result.warn,
        fail=result.fail,
        expected_checks=result.expected_checks,
        coverage=result.coverage,
        availability=result.availability,
        insufficient_reason=result.insufficient_reason,
    )


def fleet_health(
    db: Session, apps: Sequence[Application], start: datetime, end: datetime
) -> dict[int, tuple[AvailabilityOut, int | None]]:
    """Availability and p95 per application over a window, from one query for all of them."""
    ids = [app.id for app in apps]
    samples: dict[int, list[CheckSample]] = defaultdict(list)
    for row in db.execute(
        select(
            HealthCheck.application_id,
            HealthCheck.started_at,
            HealthCheck.signal,
            HealthCheck.duration_ms,
            HealthCheck.http_status,
        ).where(
            HealthCheck.application_id.in_(ids),
            HealthCheck.started_at >= start,
            HealthCheck.started_at < end,
            HealthCheck.signal.in_(CONCLUSIVE_SIGNALS),
        )
    ):
        samples[row.application_id].append(
            CheckSample(row.started_at, row.signal, row.duration_ms, row.http_status is not None)
        )
    pauses: dict[int | None, list[tuple[datetime, bool]]] = defaultdict(list)
    for pause in db.execute(
        select(OperationalEvent.application_id, OperationalEvent.occurred_at, OperationalEvent.type)
        .where(
            OperationalEvent.application_id.in_(ids),
            OperationalEvent.type.in_(("monitoring.paused", "monitoring.resumed")),
            OperationalEvent.occurred_at <= end,
        )
        .order_by(OperationalEvent.occurred_at, OperationalEvent.id)
    ):
        pauses[pause.application_id].append((pause.occurred_at, pause.type == "monitoring.paused"))
    result = {}
    for app in apps:
        monitored = _monitored_seconds(app, pauses[app.id], start, end)
        app_samples = samples[app.id]
        durations = [
            s.duration_ms for s in app_samples if s.responded and s.duration_ms is not None
        ]
        result[app.id] = (
            availability_out(availability(app_samples, app.interval_seconds, monitored)),
            percentile(durations, 0.95),
        )
    return result


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
    monitored = _monitored_seconds(app, _pause_history(db, app.id, now), start, now)
    result = availability(samples, app.interval_seconds, monitored)
    size = bucket_size(span)
    durations = [s.duration_ms for s in samples if s.responded and s.duration_ms is not None]
    return MetricsOut(
        window=window,
        window_start=start,
        window_end=now,
        bucket_seconds=int(size.total_seconds()),
        availability=availability_out(result),
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


REQUEST_LOG_EVENT = "http.request"


def request_metrics(db: Session, app: Application, window: str, now: datetime) -> RequestMetricsOut:
    """Requests and 5xx responses per bucket, counted from the application's own request logs.

    Grouped in SQL: an application can ship far more log entries than it has health checks.
    """
    span = WINDOWS[window]
    start = now - span
    size = bucket_size(span)
    seconds = int(size.total_seconds())
    status = cast(func.json_extract(LogEntry.attributes, "$.status_code"), Integer)
    epoch = cast(func.strftime("%s", LogEntry.timestamp), Integer)
    index = cast((epoch - int(start.timestamp())) / seconds, Integer).label("bucket")
    rows = db.execute(
        select(
            index,
            func.count().label("requests"),
            func.sum(case((status >= 500, 1), else_=0)).label("errors"),
        )
        .where(
            LogEntry.application_id == app.id,
            LogEntry.event == REQUEST_LOG_EVENT,
            LogEntry.timestamp >= start,
            LogEntry.timestamp < now,
            # Not "CAST ... IS NOT NULL": SQLite casts non-numeric text to 0.
            func.json_type(LogEntry.attributes, "$.status_code") == "integer",
        )
        .group_by(index)
    ).all()
    counted = {row.bucket: (row.requests, row.errors or 0) for row in rows}
    count = max(1, ceil(span / size))
    return RequestMetricsOut(
        window=window,
        window_start=start,
        window_end=now,
        bucket_seconds=seconds,
        has_data=bool(rows),
        requests=sum(r for r, _ in counted.values()),
        errors=sum(e for _, e in counted.values()),
        buckets=[
            RequestBucketOut(
                start=start + i * size,
                requests=counted.get(i, (0, 0))[0],
                errors=counted.get(i, (0, 0))[1],
            )
            for i in range(count)
        ],
        source=(
            f"Counted from '{REQUEST_LOG_EVENT}' log entries with a numeric status_code attribute "
            "that the application sends to CloudOps Monitor. Requests it did not log are not "
            "counted."
        ),
    )
