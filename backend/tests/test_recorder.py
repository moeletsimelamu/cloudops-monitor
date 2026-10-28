from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cloudops.db.models import Application, HealthCheck, Incident, OperationalEvent
from cloudops.domain.classification import Observation, ProbeError
from cloudops.domain.health_state import Transition
from cloudops.monitoring.recorder import RecordedCheck, record_check
from tests.factories import error, http, make_application, migrated_session_factory, ok

T0 = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    with migrated_session_factory(tmp_path) as factory, factory() as session:
        yield session


@pytest.fixture
def app(db: Session) -> Application:
    application = make_application(db)
    db.commit()
    return application


def _run(db: Session, app: Application, *observations: Observation) -> list[RecordedCheck]:
    """Record checks one minute apart, continuing after any already stored."""
    results = []
    for observation in observations:
        result = record_check(db, app.id, observation, T0 + timedelta(minutes=len_checks(db)))
        assert result is not None
        results.append(result)
        db.commit()
    return results


def len_checks(db: Session) -> int:
    return len(db.scalars(select(HealthCheck.id)).all())


def _events(db: Session) -> list[str]:
    return [e.type for e in db.scalars(select(OperationalEvent).order_by(OperationalEvent.id))]


def test_first_healthy_check_sets_state_without_events(db: Session, app: Application) -> None:
    [result] = _run(db, app, ok())
    assert (result.state_before, result.state_after) == ("unknown", "healthy")
    assert result.transition is Transition.FIRST_HEALTHY
    db.refresh(app)
    assert app.health_state == "healthy"
    assert app.last_success_at == T0
    assert app.last_observed_at == T0
    assert app.last_attempt_at == T0
    assert app.state_since == T0
    assert _events(db) == []
    check = db.scalars(select(HealthCheck)).one()
    assert (check.signal, check.http_status, check.duration_ms) == ("ok", 200, 40)
    assert check.resolved_ip == "127.0.0.1"
    assert check.state_after == "healthy"


def test_a_failure_episode_produces_one_incident_and_three_events(
    db: Session, app: Application
) -> None:
    # healthy, healthy, 500, 500 (degraded), 503, 503 (unavailable), 500, ok, ok (recovered)
    _run(db, app, ok(), ok(), http(500), http(500))
    db.refresh(app)
    assert app.health_state == "degraded"
    assert app.last_failure_reason == "server_error"

    _run(db, app, http(503), http(503))
    db.refresh(app)
    assert app.health_state == "unavailable"

    _run(db, app, http(500), http(500), ok(), ok())
    db.refresh(app)
    assert app.health_state == "healthy"

    assert _events(db) == [
        "health.degraded",
        "health.escalated",
        "health.improved",
        "health.recovered",
    ]
    incident = db.scalars(select(Incident)).one()
    assert incident.worst_state == "unavailable"
    assert incident.opening_reason == "server_error"
    assert incident.opened_at == T0 + timedelta(minutes=3)
    assert incident.closed_at == T0 + timedelta(minutes=9)
    events = db.scalars(select(OperationalEvent)).all()
    assert {e.incident_id for e in events} == {incident.id}
    recovered = events[-1]
    assert recovered.summary == "Orders Api recovered after 6 min"
    assert recovered.details["incident_duration_seconds"] == 360
    assert events[0].summary == "Orders Api is degraded: HTTP 500: the application is failing"
    assert events[0].severity == "warning"
    assert events[1].severity == "critical"


def test_a_persistent_failure_does_not_repeat_events(db: Session, app: Application) -> None:
    _run(db, app, ok(), *[http(503)] * 20)
    assert _events(db) == ["health.unavailable"]
    assert len(db.scalars(select(Incident)).all()) == 1


def test_first_check_failing_opens_an_incident_immediately(db: Session, app: Application) -> None:
    _run(db, app, error(ProbeError.CONNECTION_FAILED))
    db.refresh(app)
    assert app.health_state == "unavailable"
    assert _events(db) == ["health.unavailable"]


def test_inconclusive_checks_are_stored_but_change_nothing(db: Session, app: Application) -> None:
    _run(db, app, ok())
    _run(db, app, error(ProbeError.TARGET_BLOCKED, "resolved to 10.0.0.1"))
    _run(db, app, error(ProbeError.TARGET_BLOCKED, "resolved to 10.0.0.1"))
    _run(db, app, error(ProbeError.MONITOR_ERROR))
    db.refresh(app)
    assert app.health_state == "healthy"
    assert app.last_observed_at == T0  # freshness only counts conclusive checks
    assert app.last_attempt_at == T0 + timedelta(minutes=3)
    signals = [c.signal for c in db.scalars(select(HealthCheck).order_by(HealthCheck.id))]
    assert signals == ["ok", "inconclusive", "inconclusive", "inconclusive"]
    # One event for the run of blocked checks, not one per check.
    assert _events(db) == ["monitoring.target_blocked"]


def test_inconclusive_checks_do_not_break_a_failure_run(db: Session, app: Application) -> None:
    _run(db, app, ok(), http(500), error(ProbeError.MONITOR_ERROR), http(500))
    db.refresh(app)
    assert app.health_state == "degraded"


def test_one_blip_does_not_open_an_incident(db: Session, app: Application) -> None:
    _run(db, app, ok(), http(500), ok(), http(500), ok())
    db.refresh(app)
    assert app.health_state == "healthy"
    assert _events(db) == []


def test_thresholds_come_from_the_application(db: Session) -> None:
    app = make_application(db, "strict-api", failure_threshold=1, recovery_threshold=3)
    db.commit()
    _run(db, app, ok(), http(500))
    db.refresh(app)
    assert app.health_state == "degraded"
    _run(db, app, ok(), ok())
    db.refresh(app)
    assert app.health_state == "degraded"
    _run(db, app, ok())
    db.refresh(app)
    assert app.health_state == "healthy"


def test_slow_threshold_comes_from_the_application(db: Session) -> None:
    app = make_application(db, "slow-api", slow_threshold_ms=100)
    db.commit()
    [result] = _run(db, app, ok(duration_ms=250))
    assert result.outcome.reason == "slow_response"


def test_deleted_application_is_ignored(db: Session, app: Application) -> None:
    db.delete(app)
    db.commit()
    assert record_check(db, app.id, ok(), T0) is None
