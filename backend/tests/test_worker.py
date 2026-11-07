"""The monitoring worker with a controllable clock and probe (no network)."""

import asyncio
import random
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cloudops.config import Settings
from cloudops.db.models import Application, HealthCheck, LogEntry, OperationalEvent, SystemState
from cloudops.db.session import session_scope
from cloudops.domain.classification import Observation
from cloudops.monitoring import retention as retention_module
from cloudops.monitoring import worker as worker_module
from cloudops.monitoring.runner import CheckRunner
from cloudops.monitoring.target_policy import TargetPolicy
from cloudops.monitoring.worker import HEARTBEAT_KEY, MonitoringWorker
from tests.conftest import make_settings
from tests.factories import http, make_application, migrated_session_factory, ok

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class FakeProbe:
    """Returns a configured observation per URL, optionally after a delay."""

    def __init__(self) -> None:
        self.responses: dict[str, Observation] = {}
        self.delays: dict[str, float] = {}
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def __call__(
        self, url: str, *, timeout_seconds: float, policy: TargetPolicy
    ) -> Observation:
        self.calls.append(url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delays.get(url, 0))
            return self.responses.get(url, ok())
        finally:
            self.active -= 1


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    with migrated_session_factory(tmp_path) as session_factory:
        yield session_factory


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path, monitor_max_concurrency=10)


def _worker(
    factory: sessionmaker[Session], settings: Settings, probe: FakeProbe, clock: Clock
) -> MonitoringWorker:
    runner = CheckRunner(factory, settings, probe_fn=probe, clock=clock)
    return MonitoringWorker(factory, settings, runner, clock=clock, rng=random.Random(1))


def _add(factory: sessionmaker[Session], slug: str, **overrides: object) -> int:
    with session_scope(factory) as db:
        overrides.setdefault("check_url", f"http://{slug}:8310/health")
        return make_application(db, slug, **overrides).id


def _checks(factory: sessionmaker[Session]) -> list[HealthCheck]:
    with session_scope(factory) as db:
        return list(db.scalars(select(HealthCheck).order_by(HealthCheck.id)))


async def test_due_applications_are_checked_and_rescheduled_with_jitter(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    probe, clock = FakeProbe(), Clock()
    app_id = _add(factory, "orders-api", interval_seconds=60)
    worker = _worker(factory, settings, probe, clock)

    assert await worker.tick() == 1
    await worker.wait_for_checks()
    assert len(_checks(factory)) == 1
    with session_scope(factory) as db:
        app = db.get(Application, app_id)
        assert app is not None
        assert app.health_state == "healthy"
        assert app.next_check_at is not None
        delay = (app.next_check_at - NOW).total_seconds()
    assert 60 <= delay <= 66  # interval plus up to 10% jitter

    assert await worker.tick() == 0  # not due again yet
    clock.now = NOW + timedelta(seconds=67)
    assert await worker.tick() == 1


async def test_paused_applications_are_not_checked(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    probe, clock = FakeProbe(), Clock()
    _add(factory, "paused-api", monitoring_enabled=False)
    worker = _worker(factory, settings, probe, clock)
    assert await worker.tick() == 0
    assert probe.calls == []


async def test_a_slow_target_does_not_delay_the_others(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    probe, clock = FakeProbe(), Clock()
    _add(factory, "slow-api")
    _add(factory, "fast-api")
    probe.delays["http://slow-api:8310/health"] = 1.0
    worker = _worker(factory, settings, probe, clock)

    await worker.tick()
    await asyncio.sleep(0.3)
    recorded = {c.application_id for c in _checks(factory)}
    assert len(recorded) == 1  # the fast one is done while the slow one is still waiting

    # A tick while the slow check runs does not start it a second time.
    clock.now = NOW + timedelta(minutes=5)
    assert await worker.tick() == 1  # only the fast application, which is due again
    await worker.wait_for_checks()
    assert probe.calls.count("http://slow-api:8310/health") == 1


async def test_concurrency_is_capped(tmp_path: Path, factory: sessionmaker[Session]) -> None:
    settings = make_settings(tmp_path, monitor_max_concurrency=2)
    probe, clock = FakeProbe(), Clock()
    for index in range(6):
        slug = f"api-{index}"
        _add(factory, slug)
        probe.delays[f"http://{slug}:8310/health"] = 0.2
    worker = _worker(factory, settings, probe, clock)
    await worker.tick()
    await worker.wait_for_checks()
    assert len(probe.calls) == 6
    assert probe.max_active == 2


async def test_failures_flow_through_to_state_and_events(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    probe, clock = FakeProbe(), Clock()
    app_id = _add(factory, "orders-api", interval_seconds=15)
    url = "http://orders-api:8310/health"
    worker = _worker(factory, settings, probe, clock)
    for minute, observation in enumerate([ok(), http(500), http(500), ok(), ok()]):
        probe.responses[url] = observation
        clock.now = NOW + timedelta(minutes=minute)
        await worker.tick()
        await worker.wait_for_checks()
    with session_scope(factory) as db:
        app = db.get(Application, app_id)
        assert app is not None
        assert app.health_state == "healthy"
        events = list(db.scalars(select(OperationalEvent.type).order_by(OperationalEvent.id)))
    assert events == ["health.degraded", "health.recovered"]


async def test_overdue_checks_run_once_after_downtime(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    probe, clock = FakeProbe(), Clock()
    _add(factory, "orders-api", next_check_at=NOW - timedelta(hours=2))
    worker = _worker(factory, settings, probe, clock)
    assert await worker.tick() == 1
    await worker.wait_for_checks()
    assert await worker.tick() == 0  # missed slots are not replayed


async def test_start_records_the_gap_since_the_last_heartbeat(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with session_scope(factory) as db:
        db.add(
            SystemState(
                key=HEARTBEAT_KEY,
                value={"at": (NOW - timedelta(minutes=37)).isoformat()},
                updated_at=NOW - timedelta(minutes=37),
            )
        )
    worker = _worker(factory, settings, FakeProbe(), Clock())
    await worker.start()
    await worker.stop()
    with session_scope(factory) as db:
        event = db.scalars(select(OperationalEvent)).one()
        heartbeat = db.get(SystemState, HEARTBEAT_KEY)
        assert heartbeat is not None
        assert heartbeat.updated_at == NOW
    assert event.type == "monitoring.worker_started"
    assert event.severity == "warning"
    assert event.summary == "Monitoring resumed after a 37 min gap: no checks ran during that time"
    assert event.details["gap_seconds"] == 37 * 60


async def test_first_start_is_informational(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    worker = _worker(factory, settings, FakeProbe(), Clock())
    await worker.start()
    assert worker.running
    await worker.stop()
    assert not worker.running
    with session_scope(factory) as db:
        event = db.scalars(select(OperationalEvent)).one()
    assert (event.severity, event.summary) == ("info", "Monitoring worker started")


async def test_the_loop_survives_a_failing_tick(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe, clock = FakeProbe(), Clock()
    _add(factory, "orders-api")
    runner = CheckRunner(factory, settings, probe_fn=probe, clock=clock)
    worker = MonitoringWorker(factory, settings, runner, clock=clock, tick_seconds=0.05)
    original = worker._due_checks
    failures = {"left": 2}

    def flaky(now: datetime) -> object:
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("database is locked")
        return original(now)

    monkeypatch.setattr(worker, "_due_checks", flaky)
    await worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (not probe.calls or worker.consecutive_tick_failures):
        await asyncio.sleep(0.05)
    await worker.stop()
    assert probe.calls == ["http://orders-api:8310/health"]
    assert failures["left"] == 0
    assert worker.consecutive_tick_failures == 0
    assert worker.last_tick_at == NOW


async def test_health_reflects_recent_ticks(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    worker = _worker(factory, settings, FakeProbe(), Clock())
    assert not worker.is_healthy(NOW)
    await worker.start()
    await asyncio.sleep(0.1)
    assert worker.is_healthy(NOW)
    assert not worker.is_healthy(NOW + timedelta(minutes=5))
    await worker.stop()
    assert not worker.is_healthy(NOW)


async def test_retention_removes_old_records_and_caps_logs(
    tmp_path: Path, factory: sessionmaker[Session]
) -> None:
    settings = make_settings(
        tmp_path, check_retention_days=30, log_retention_days=7, log_max_rows=1000
    )
    with session_scope(factory) as db:
        app = make_application(db)
        for age_days in (31, 29):
            db.add(
                HealthCheck(
                    application_id=app.id,
                    started_at=NOW - timedelta(days=age_days),
                    signal="ok",
                    state_after="healthy",
                )
            )
        db.add(
            LogEntry(
                application_id=app.id,
                timestamp=NOW - timedelta(days=8),
                severity=20,
                message="old",
            )
        )
        # Inserted oldest first, so higher IDs are newer, as in real ingestion.
        for index in reversed(range(1005)):
            db.add(
                LogEntry(
                    application_id=app.id,
                    timestamp=NOW - timedelta(minutes=index),
                    severity=20,
                    message=f"entry {index}",
                )
            )
    worker = _worker(factory, settings, FakeProbe(), Clock())
    await worker.retention()
    result = worker.last_retention
    assert result is not None
    assert result.health_checks == 1
    assert result.log_entries == 1 + 5
    with session_scope(factory) as db:
        assert len(db.scalars(select(HealthCheck.id)).all()) == 1
        messages = set(db.scalars(select(LogEntry.message)))
    assert len(messages) == 1000
    assert "old" not in messages
    assert "entry 0" in messages  # the newest entries are kept
    assert "entry 1004" not in messages


async def test_retention_removes_old_checks_of_every_application(
    tmp_path: Path, factory: sessionmaker[Session]
) -> None:
    """Retention deletes application by application (to use the index); none is missed."""
    with session_scope(factory) as db:
        for slug in ("orders-api", "billing-api", "search-api"):
            app = make_application(db, slug)
            for age_days in (45, 31, 29, 1):
                db.add(
                    HealthCheck(
                        application_id=app.id,
                        started_at=NOW - timedelta(days=age_days),
                        signal="ok",
                        state_after="healthy",
                    )
                )
    worker = _worker(
        factory, make_settings(tmp_path, check_retention_days=30), FakeProbe(), Clock()
    )
    await worker.retention()
    assert worker.last_retention is not None
    assert worker.last_retention.health_checks == 6
    with session_scope(factory) as db:
        ages = sorted((NOW - c.started_at).days for c in db.scalars(select(HealthCheck)))
    assert ages == [1, 1, 1, 29, 29, 29]


async def test_checks_keep_being_scheduled_while_retention_runs(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the loop awaited retention, so no check started while it ran (18 s on a
    database of 1.15 million checks)."""
    started, release = threading.Event(), threading.Event()
    real_retention = retention_module.run_retention

    def slow_retention(*args: object) -> object:
        started.set()
        release.wait(timeout=10)
        return real_retention(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_module, "run_retention", slow_retention)
    probe, clock = FakeProbe(), Clock()
    runner = CheckRunner(factory, settings, probe_fn=probe, clock=clock)
    worker = MonitoringWorker(factory, settings, runner, clock=clock, tick_seconds=0.05)
    await worker.start()
    try:
        while not started.is_set():
            await asyncio.sleep(0.01)
        _add(factory, "orders-api")  # due only once retention is under way
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not probe.calls:
            await asyncio.sleep(0.05)
        assert probe.calls, "a check ran while retention was still deleting"
        assert worker.last_retention is None
        assert not worker.start_retention(), "only one retention run at a time"
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and worker.last_retention is None:
        await asyncio.sleep(0.05)
    await worker.stop()
    assert worker.last_retention is not None


async def test_a_failing_retention_is_logged_and_does_not_count_as_a_failed_tick(
    factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*_args: object) -> object:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(worker_module, "run_retention", broken)
    probe, clock = FakeProbe(), Clock()
    runner = CheckRunner(factory, settings, probe_fn=probe, clock=clock)
    worker = MonitoringWorker(factory, settings, runner, clock=clock, tick_seconds=0.05)
    await worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not any(
        getattr(r, "fields", {}).get("event") == "retention.failed" for r in caplog.records
    ):
        await asyncio.sleep(0.05)
    await worker.stop()
    assert any(getattr(r, "fields", {}).get("event") == "retention.failed" for r in caplog.records)
    assert worker.consecutive_tick_failures == 0
    assert worker.last_tick_at == NOW


async def test_a_worker_that_just_started_is_healthy_but_one_that_never_ticks_is_not(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: readiness failed in the instant between start-up and the first tick."""
    worker = _worker(factory, settings, FakeProbe(), Clock())

    async def never_finishes() -> int:
        await asyncio.sleep(60)
        return 0

    monkeypatch.setattr(worker, "tick", never_finishes)
    await worker.start()
    await asyncio.sleep(0)
    assert worker.last_tick_at is None
    assert worker.is_healthy(NOW)
    assert not worker.is_healthy(NOW + timedelta(minutes=5))
    await worker.stop()


async def test_stop_waits_for_database_work_in_progress(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: stop() returned while a thread was still using the database, so the engine
    was disposed under it and its connection leaked."""
    worker = _worker(factory, settings, FakeProbe(), Clock())
    started, finished = threading.Event(), threading.Event()

    def slow_query(now: datetime) -> list[object]:
        started.set()
        time.sleep(0.3)
        finished.set()
        return []

    monkeypatch.setattr(worker, "_due_checks", slow_query)
    await worker.start()
    while not started.is_set():
        await asyncio.sleep(0.01)
    await worker.stop()
    assert finished.is_set()
