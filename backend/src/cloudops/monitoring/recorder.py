"""Persisting a check: the check row, the application's state, incidents and events, in one
transaction (docs/ARCHITECTURE.md §3.4-§3.6)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cloudops.db.models import Application, HealthCheck, Incident
from cloudops.domain.classification import CONCLUSIVE_SIGNALS, CheckOutcome, Observation, classify
from cloudops.domain.health_state import HealthState, Transition, next_state, transition
from cloudops.events import Severity, humanize_duration, record_event


@dataclass(frozen=True)
class RecordedCheck:
    check_id: int
    outcome: CheckOutcome
    state_before: HealthState
    state_after: HealthState
    transition: Transition


def _recent_conclusive_signals(db: Session, application_id: int, limit: int) -> list[str]:
    """The most recent conclusive signals, oldest first."""
    if limit <= 0:
        return []
    rows = db.scalars(
        select(HealthCheck.signal)
        .where(
            HealthCheck.application_id == application_id,
            HealthCheck.signal.in_(CONCLUSIVE_SIGNALS),
        )
        .order_by(HealthCheck.started_at.desc(), HealthCheck.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def _previous_reason(db: Session, application_id: int) -> str | None:
    return db.scalars(
        select(HealthCheck.reason)
        .where(HealthCheck.application_id == application_id)
        .order_by(HealthCheck.started_at.desc(), HealthCheck.id.desc())
        .limit(1)
    ).first()


def _open_incident(db: Session, application_id: int) -> Incident | None:
    return db.scalars(
        select(Incident).where(
            Incident.application_id == application_id, Incident.closed_at.is_(None)
        )
    ).one_or_none()


def record_check(
    db: Session,
    application_id: int,
    observation: Observation,
    started_at: datetime,
    *,
    manual: bool = False,
) -> RecordedCheck | None:
    """Store one check and everything it changes. None if the application no longer exists."""
    app = db.get(Application, application_id)
    if app is None:
        return None
    outcome = classify(observation, app.slow_threshold_ms)
    before: HealthState = app.health_state  # type: ignore[assignment]
    after: HealthState = before
    previous_reason = _previous_reason(db, app.id)

    app.last_attempt_at = started_at
    if outcome.signal in CONCLUSIVE_SIGNALS:
        window = max(app.failure_threshold, app.recovery_threshold) - 1
        signals = [*_recent_conclusive_signals(db, app.id, window), outcome.signal]
        after = next_state(before, signals, app.failure_threshold, app.recovery_threshold)
        app.last_observed_at = started_at
        if outcome.signal == "ok":
            app.last_success_at = started_at
        else:
            app.last_failure_at = started_at
            app.last_failure_reason = outcome.reason

    check = HealthCheck(
        application_id=app.id,
        started_at=started_at,
        duration_ms=observation.duration_ms,
        http_status=observation.status_code,
        signal=outcome.signal,
        reason=outcome.reason,
        detail=outcome.detail,
        resolved_ip=observation.resolved_ip,
        state_after=after,
    )
    db.add(check)
    db.flush()

    change = transition(before, after)
    if change is not Transition.NONE:
        app.health_state = after
        app.state_since = started_at
        _apply_transition(db, app, change, before, after, outcome, check, manual)

    if outcome.reason == "target_blocked" and previous_reason != "target_blocked":
        # Once per run of blocked checks, not on every check.
        record_event(
            db,
            type="monitoring.target_blocked",
            severity="warning",
            summary=f"{app.name}: check blocked by the target policy. {outcome.detail or ''}",
            occurred_at=started_at,
            application_id=app.id,
            details={"check_id": check.id, "detail": outcome.detail},
        )
    return RecordedCheck(check.id, outcome, before, after, change)


def _apply_transition(
    db: Session,
    app: Application,
    change: Transition,
    before: HealthState,
    after: HealthState,
    outcome: CheckOutcome,
    check: HealthCheck,
    manual: bool,
) -> None:
    details: dict[str, Any] = {
        "check_id": check.id,
        "previous_state": before,
        "state": after,
        "signal": outcome.signal,
        "reason": outcome.reason,
        "http_status": check.http_status,
        "duration_ms": check.duration_ms,
        "manual_check": manual,
    }
    evidence = outcome.detail or outcome.reason or "health check failed"
    occurred_at = check.started_at

    if change is Transition.FIRST_HEALTHY:
        return
    if change is Transition.OPENED:
        incident = Incident(
            application_id=app.id,
            opened_at=occurred_at,
            worst_state=after,
            opening_reason=outcome.reason,
        )
        db.add(incident)
        db.flush()
        severity: Severity = "critical" if after == "unavailable" else "warning"
        record_event(
            db,
            type=f"health.{after}",
            severity=severity,
            summary=f"{app.name} is {after}: {evidence}",
            occurred_at=occurred_at,
            application_id=app.id,
            incident_id=incident.id,
            details=details,
        )
        return

    open_incident = _open_incident(db, app.id)
    incident_id = open_incident.id if open_incident else None
    if change is Transition.ESCALATED:
        if open_incident:
            open_incident.worst_state = "unavailable"
        record_event(
            db,
            type="health.escalated",
            severity="critical",
            summary=f"{app.name} is now unavailable: {evidence}",
            occurred_at=occurred_at,
            application_id=app.id,
            incident_id=incident_id,
            details=details,
        )
    elif change is Transition.IMPROVED:
        record_event(
            db,
            type="health.improved",
            severity="warning",
            summary=f"{app.name} is responding again but still degraded: {evidence}",
            occurred_at=occurred_at,
            application_id=app.id,
            incident_id=incident_id,
            details=details,
        )
    elif change is Transition.RECOVERED:
        duration = ""
        if open_incident:
            open_incident.closed_at = occurred_at
            elapsed = occurred_at - open_incident.opened_at
            details["incident_duration_seconds"] = int(elapsed.total_seconds())
            duration = f" after {humanize_duration(elapsed)}"
        record_event(
            db,
            type="health.recovered",
            severity="info",
            summary=f"{app.name} recovered{duration}",
            occurred_at=occurred_at,
            application_id=app.id,
            incident_id=incident_id,
            details=details,
        )
