"""Syncing deployment records from a fake GitHub into a real database."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cloudops.db.models import Application, Deployment, OperationalEvent, SystemState
from cloudops.db.session import session_scope
from cloudops.deployments.sync import DeploymentSync, sync_state_key
from cloudops.monitoring.runner import CheckRunner
from cloudops.monitoring.worker import MonitoringWorker
from tests.conftest import make_settings
from tests.factories import make_application, migrated_session_factory
from tests.github_fake import FakeGitHub, run_payload

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    with migrated_session_factory(tmp_path) as session_factory:
        yield session_factory


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def sync(factory: sessionmaker[Session], github: FakeGitHub, tmp_path: Path) -> DeploymentSync:
    return DeploymentSync(
        factory, make_settings(tmp_path), client=github.client(), clock=lambda: NOW
    )


@pytest.fixture
def app_id(factory: sessionmaker[Session]) -> int:
    with session_scope(factory) as db:
        return make_application(
            db, "orders-api", github_repo="acme/orders", deploy_workflow="deploy.yml"
        ).id


def _deployments(factory: sessionmaker[Session]) -> list[Deployment]:
    with session_scope(factory) as db:
        return list(db.scalars(select(Deployment).order_by(Deployment.id)))


def _events(factory: sessionmaker[Session]) -> list[OperationalEvent]:
    with session_scope(factory) as db:
        return list(db.scalars(select(OperationalEvent).order_by(OperationalEvent.id)))


async def test_a_completed_run_becomes_a_record_and_one_event(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [run_payload(812)]
    result = await sync.sync_application(app_id)
    assert (result.status, result.created, result.updated) == ("synced", 1, 0)

    record = _deployments(factory)[0]
    assert record.source == "github_actions"
    assert record.external_id == "812"
    assert record.attempt == 1
    assert record.run_number == 57
    assert record.commit_sha == "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    assert record.branch == "main"
    assert record.trigger == "push"
    assert record.actor == "octocat"
    assert record.title == "Fix order totals"
    assert record.workflow_name == "Deploy"
    assert record.outcome == "succeeded"
    assert record.queued_at == datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
    assert record.started_at == datetime(2026, 10, 27, 14, 0, 5, tzinfo=UTC)
    assert record.completed_at == datetime(2026, 10, 27, 14, 3, tzinfo=UTC)
    assert record.url == "https://github.com/acme/orders/actions/runs/812"

    [event] = _events(factory)
    assert event.type == "deployment.succeeded"
    assert event.severity == "info"
    assert event.summary == "Orders Api: deployment #57 (a1b2c3d) succeeded"
    assert event.occurred_at == record.completed_at  # when it happened, not when it was seen
    assert event.deployment_id == record.id
    assert event.application_id == app_id


async def test_syncing_again_changes_nothing(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [run_payload(812), run_payload(811, run_number=56)]
    await sync.sync_application(app_id)
    github.rate_remaining = "4998"
    result = await sync.sync_application(app_id)
    assert result.status == "not_modified"  # the ETag matched
    assert "if-none-match" in github.requests[1].headers
    assert len(_deployments(factory)) == 2
    assert len(_events(factory)) == 2
    assert sync.rate_remaining == 4998


async def test_same_runs_without_an_etag_are_idempotent_too(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [run_payload(812)]
    await sync.sync_application(app_id)
    with session_scope(factory) as db:
        state = db.get(SystemState, sync_state_key(app_id))
        assert state is not None
        state.value = {**state.value, "etag": None}
    result = await sync.sync_application(app_id)
    assert (result.status, result.created, result.updated) == ("synced", 0, 0)
    assert len(_deployments(factory)) == 1
    assert len(_events(factory)) == 1


async def test_a_run_is_followed_from_queued_to_finished(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [
        run_payload(812, status="queued", conclusion=None, run_started_at=None),
    ]
    await sync.sync_application(app_id)
    assert _deployments(factory)[0].outcome == "queued"
    assert _deployments(factory)[0].completed_at is None
    assert [e.type for e in _events(factory)] == ["deployment.started"]

    github.runs = [run_payload(812, status="in_progress", conclusion=None)]
    result = await sync.sync_application(app_id)
    assert result.updated == 1
    assert _deployments(factory)[0].outcome == "in_progress"
    assert [e.type for e in _events(factory)] == ["deployment.started"]  # not "started" twice

    github.runs = [run_payload(812, status="completed", conclusion="failure")]
    await sync.sync_application(app_id)
    record = _deployments(factory)[0]
    assert record.outcome == "failed"
    assert record.completed_at is not None
    events = _events(factory)
    assert [e.type for e in events] == ["deployment.started", "deployment.failed"]
    assert events[1].severity == "error"
    assert events[1].summary.endswith("failed")


async def test_a_rerun_is_a_separate_attempt(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [run_payload(812, conclusion="failure")]
    await sync.sync_application(app_id)
    github.runs = [run_payload(812, run_attempt=2, conclusion="success")]
    await sync.sync_application(app_id)
    assert [(d.attempt, d.outcome) for d in _deployments(factory)] == [
        (1, "failed"),
        (2, "succeeded"),
    ]


async def test_two_applications_can_share_a_workflow(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    with session_scope(factory) as db:
        other = make_application(
            db, "billing-api", github_repo="acme/orders", deploy_workflow="deploy.yml"
        ).id
    github.runs = [run_payload(812)]
    await sync.sync_application(app_id)
    await sync.sync_application(other)
    assert sorted(d.application_id for d in _deployments(factory)) == sorted([app_id, other])


async def test_untrusted_values_are_bounded_and_links_must_be_https(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [
        run_payload(
            812,
            display_title="t" * 1000,
            html_url="javascript:alert(1)",
            head_branch="b" * 1000,
            actor=None,
        )
    ]
    await sync.sync_application(app_id)
    record = _deployments(factory)[0]
    assert record.title is not None
    assert len(record.title) == 300
    assert record.branch is not None
    assert len(record.branch) == 255
    assert record.url is None
    assert record.actor is None


async def test_repeated_failures_record_one_event_and_recovery_another(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.failure = lambda _request: httpx2.Response(500)
    for _ in range(4):
        result = await sync.sync_application(app_id)
        assert (result.status, result.error_kind) == ("failed", "unavailable")
    events = _events(factory)
    assert [e.type for e in events] == ["deployments.sync_failed"]
    assert events[0].severity == "warning"
    assert "HTTP 500" in events[0].summary

    github.failure = None
    github.runs = [run_payload(812)]
    assert (await sync.sync_application(app_id)).status == "synced"
    assert [e.type for e in _events(factory)] == [
        "deployments.sync_failed",
        "deployment.succeeded",
        "deployments.sync_recovered",
    ]
    with session_scope(factory) as db:
        state = db.get(SystemState, sync_state_key(app_id))
        assert state is not None
        assert state.value["last_error_kind"] is None
        assert state.value["last_success_at"] == NOW.isoformat()


async def test_a_failure_keeps_existing_records_and_the_etag(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [run_payload(812)]
    await sync.sync_application(app_id)
    github.failure = lambda _request: httpx2.Response(503)
    await sync.sync_application(app_id)
    assert len(_deployments(factory)) == 1
    github.failure = None
    assert (await sync.sync_application(app_id)).status == "not_modified"


async def test_rate_limit_stops_requests_until_it_resets(
    factory: sessionmaker[Session], github: FakeGitHub, tmp_path: Path, app_id: int
) -> None:
    clock = [NOW]
    sync = DeploymentSync(
        factory, make_settings(tmp_path), client=github.client(), clock=lambda: clock[0]
    )
    reset = NOW + timedelta(minutes=30)
    github.failure = lambda _request: httpx2.Response(
        403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(reset.timestamp()))},
    )
    assert (await sync.sync_application(app_id)).error_kind == "rate_limited"
    assert len(github.requests) == 1

    skipped = await sync.sync_application(app_id)
    assert (skipped.status, skipped.error_kind) == ("skipped", "rate_limited")
    assert len(github.requests) == 1  # GitHub was left alone

    github.failure = None
    clock[0] = reset + timedelta(seconds=1)
    assert (await sync.sync_application(app_id)).status == "synced"


async def test_changing_the_source_does_not_reuse_the_old_etag(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    github.runs = [run_payload(812)]
    await sync.sync_application(app_id)
    with session_scope(factory) as db:
        app = db.get(Application, app_id)
        assert app is not None
        app.deploy_workflow = "release.yml"
    await sync.sync_application(app_id)
    assert "if-none-match" not in github.requests[1].headers
    assert github.requests[1].url.path.endswith("/workflows/release.yml/runs")


async def test_sync_all_covers_only_configured_applications(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    with session_scope(factory) as db:
        make_application(db, "no-source-api")
    results = await sync.sync_all()
    assert [r.application_id for r in results] == [app_id]
    assert len(github.requests) == 1


async def test_the_branch_filter_is_sent(
    sync: DeploymentSync, github: FakeGitHub, factory: sessionmaker[Session], app_id: int
) -> None:
    with session_scope(factory) as db:
        app = db.get(Application, app_id)
        assert app is not None
        app.deploy_branch = "release/1.x"
    await sync.sync_application(app_id)
    assert github.requests[0].url.params["branch"] == "release/1.x"


async def test_the_worker_starts_one_sync_at_a_time(
    factory: sessionmaker[Session], github: FakeGitHub, tmp_path: Path, app_id: int
) -> None:
    settings = make_settings(tmp_path)
    sync = DeploymentSync(factory, settings, client=github.client())
    worker = MonitoringWorker(
        factory, settings, CheckRunner(factory, settings), deployment_sync=sync
    )
    github.runs = [run_payload(812)]
    assert worker.start_deployment_sync() is True
    assert worker.start_deployment_sync() is False  # the first is still running
    await worker.wait_for_checks()
    assert len(github.requests) == 1
    assert len(_deployments(factory)) == 1
    assert worker.start_deployment_sync() is True
    await worker.wait_for_checks()


async def test_a_worker_without_a_sync_does_nothing(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    worker = MonitoringWorker(factory, settings, CheckRunner(factory, settings))
    assert worker.start_deployment_sync() is False
