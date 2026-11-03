"""Deployments API, correlation, fleet-wide incidents and the overview."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from cloudops.db.models import Application, Deployment, SystemState
from cloudops.db.session import session_scope
from cloudops.db.types import utc_now
from cloudops.deployments.sync import sync_state_key
from cloudops.domain.correlation import find_preceding_deployment
from cloudops.main import create_app
from cloudops.monitoring.recorder import record_check
from tests.conftest import OPERATOR_TOKEN, make_settings, operator_headers
from tests.factories import http, make_application, ok
from tests.github_fake import FakeGitHub, run_payload

T0 = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


@dataclass
class Times:
    started_at: datetime | None
    completed_at: datetime | None


@pytest.mark.parametrize(
    ("deployments", "relation", "minutes"),
    [
        ([Times(T0 - timedelta(minutes=10), T0 - timedelta(minutes=4))], "completed_before", 4),
        ([Times(T0 - timedelta(minutes=3), None)], "in_progress", 3),
        # Finished after the incident opened: it was running at the time.
        ([Times(T0 - timedelta(minutes=3), T0 + timedelta(minutes=2))], "in_progress", 3),
        # The running one is preferred over one that finished earlier.
        (
            [
                Times(T0 - timedelta(minutes=30), T0 - timedelta(minutes=20)),
                Times(T0 - timedelta(minutes=5), None),
            ],
            "in_progress",
            5,
        ),
        # The latest to finish is chosen.
        (
            [
                Times(T0 - timedelta(minutes=50), T0 - timedelta(minutes=40)),
                Times(T0 - timedelta(minutes=20), T0 - timedelta(minutes=10)),
            ],
            "completed_before",
            10,
        ),
        ([Times(T0 - timedelta(hours=2), T0 - timedelta(minutes=60))], "completed_before", 60),
    ],
)
def test_finding_the_preceding_deployment(
    deployments: list[Times], relation: str, minutes: int
) -> None:
    found = find_preceding_deployment(T0, deployments)
    assert found is not None
    assert (found.relation, found.minutes_before) == (relation, minutes)


@pytest.mark.parametrize(
    "deployments",
    [
        [],
        [Times(T0 - timedelta(hours=2), T0 - timedelta(minutes=61))],  # too long before
        [Times(T0 + timedelta(minutes=1), T0 + timedelta(minutes=5))],  # after the incident
        [Times(None, None)],  # never started
        [Times(T0 - timedelta(hours=5), None)],  # an old run never seen to finish
    ],
)
def test_no_preceding_deployment(deployments: list[Times]) -> None:
    assert find_preceding_deployment(T0, deployments) is None


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def app(tmp_path: Path, github: FakeGitHub) -> FastAPI:
    application = create_app(make_settings(tmp_path / "data", allowed_targets=["localhost:8310"]))
    application.state.deployment_sync.client = github.client()
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, headers=operator_headers()) as test_client:
        yield test_client


def _register(client: TestClient, slug: str = "orders-api", **overrides: Any) -> dict[str, Any]:
    body = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "environment": "demo",
        "check_url": "http://localhost:8310/health",
        **overrides,
    }
    response = client.post("/api/applications", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _with_source(client: TestClient, slug: str = "orders-api") -> dict[str, Any]:
    return _register(client, slug, github_repo="acme/orders", deploy_workflow="deploy.yml")


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _recent_run(run_id: int, minutes_ago: int, **overrides: Any) -> dict[str, Any]:
    now = utc_now()
    return run_payload(
        run_id,
        run_number=run_id,
        created_at=_iso(now - timedelta(minutes=minutes_ago + 3)),
        run_started_at=_iso(now - timedelta(minutes=minutes_ago + 3)),
        updated_at=_iso(now - timedelta(minutes=minutes_ago)),
        **overrides,
    )


def _sync(client: TestClient, app: FastAPI, slug: str = "orders-api") -> dict[str, Any]:
    app.state.manual_syncs.clear()
    response = client.post(f"/api/applications/{slug}/deployments/sync")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _checks(app: FastAPI, slug: str, *observations: Any, end: datetime | None = None) -> None:
    end = end or utc_now()
    with session_scope(app.state.session_factory) as db:
        application = db.scalars(select(Application).where(Application.slug == slug)).one()
        start = end - timedelta(seconds=len(observations))
        for index, observation in enumerate(observations):
            record_check(db, application.id, observation, start + timedelta(seconds=index))
            db.flush()


# --- deployments API --------------------------------------------------------------------------


def test_routes_require_the_operator(app: FastAPI, client: TestClient) -> None:
    with TestClient(app) as anonymous:
        for method, path in [
            ("GET", "/api/deployments"),
            ("GET", "/api/deployments/sources"),
            ("POST", "/api/applications/x/deployments/sync"),
            ("GET", "/api/incidents"),
            ("GET", "/api/overview"),
        ]:
            assert anonymous.request(method, path).status_code == 401, path


def test_there_is_no_route_that_starts_a_deployment(app: FastAPI) -> None:
    """The only write near deployments reads from GitHub. See docs/SECURITY.md section 6."""
    writes = [
        (path, method)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if "deploy" in path and method != "get"
    ]
    assert writes == [("/api/applications/{slug}/deployments/sync", "post")]


def test_sync_now_stores_runs_and_lists_them(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    github.runs = [_recent_run(812, 10)]
    assert _sync(client, app) == {
        "status": "synced",
        "created": 1,
        "updated": 0,
        "error_kind": None,
        "message": None,
    }
    item = client.get("/api/deployments").json()["items"][0]
    assert item["application_slug"] == "orders-api"
    assert item["environment"] == "demo"
    assert item["source"] == "github_actions"
    assert item["outcome"] == "succeeded"
    assert item["duration_seconds"] == 180
    assert item["url"].startswith("https://github.com/")


def test_sync_now_explains_itself_when_it_cannot_run(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    assert client.post("/api/applications/nope/deployments/sync").status_code == 404

    _register(client, "no-source-api")
    response = client.post("/api/applications/no-source-api/deployments/sync")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_deployment_source"

    _with_source(client)
    assert client.post("/api/applications/orders-api/deployments/sync").status_code == 200
    again = client.post("/api/applications/orders-api/deployments/sync")
    assert again.status_code == 429
    assert again.json()["error"]["code"] == "too_many_syncs"


def test_sync_now_reports_failures_in_its_result(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    github.failure = lambda _request: httpx2.Response(404)
    result = _sync(client, app)
    assert result["status"] == "failed"
    assert result["error_kind"] == "not_found"
    assert "no workflow deploy.yml in acme/orders" in result["message"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, [3, 2, 1]),
        ({"outcome": "failed"}, [2]),
        ({"branch": "release"}, [3]),
        ({"application": "orders-api"}, [3, 2, 1]),
    ],
)
def test_deployment_filters(
    client: TestClient,
    app: FastAPI,
    github: FakeGitHub,
    params: dict[str, str],
    expected: list[int],
) -> None:
    _with_source(client)
    github.runs = [
        _recent_run(1, 30),
        _recent_run(2, 20, conclusion="failure"),
        _recent_run(3, 10, head_branch="release"),
    ]
    _sync(client, app)
    items = client.get("/api/deployments", params=params).json()["items"]
    assert [i["run_number"] for i in items] == expected


def test_deployment_paging_and_validation(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    github.runs = [_recent_run(i, i * 5) for i in range(1, 6)]
    _sync(client, app)
    first = client.get("/api/deployments", params={"limit": 2}).json()
    second = client.get(
        "/api/deployments", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [i["run_number"] for i in first["items"] + second["items"]] == [1, 2, 3, 4]
    assert client.get("/api/deployments", params={"outcome": "great"}).status_code == 422
    assert client.get("/api/deployments", params={"application": "nope"}).status_code == 404


def test_sources_show_what_is_configured_and_whether_it_works(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    _register(client, "no-source-api")
    before = client.get("/api/deployments/sources").json()
    assert before["token_configured"] is False
    assert [s["application_slug"] for s in before["sources"]] == ["orders-api"]
    assert before["sources"][0]["last_attempt_at"] is None

    github.failure = lambda _request: httpx2.Response(500)
    _sync(client, app)
    failing = client.get("/api/deployments/sources").json()["sources"][0]
    assert failing["last_error_kind"] == "unavailable"
    assert failing["last_success_at"] is None

    github.failure = None
    _sync(client, app)
    body = client.get("/api/deployments/sources").json()
    assert body["sources"][0]["last_error_kind"] is None
    assert body["sources"][0]["last_success_at"] is not None
    assert body["rate_limit_remaining"] == 4999


def test_the_token_is_never_returned(tmp_path: Path) -> None:
    token = "github_pat_" + "Z" * 40
    settings = make_settings(tmp_path / "data", github_token=token)
    with TestClient(create_app(settings), headers=operator_headers()) as client:
        body = client.get("/api/deployments/sources")
        assert body.json()["token_configured"] is True
        assert token not in body.text
        assert OPERATOR_TOKEN not in body.text


def test_removing_an_application_removes_its_sync_state(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    created = _with_source(client)
    _sync(client, app)
    assert client.delete("/api/applications/orders-api").status_code == 204
    with session_scope(app.state.session_factory) as db:
        assert db.get(SystemState, sync_state_key(created["id"])) is None
        assert db.scalars(select(Deployment)).all() == []


# --- correlation ------------------------------------------------------------------------------


def test_an_incident_names_the_deployment_before_it(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    github.runs = [_recent_run(812, 4)]
    _sync(client, app)
    _checks(app, "orders-api", ok(), http(500), http(500))

    application = client.get("/api/applications/orders-api").json()
    preceding = application["open_incident"]["preceding_deployment"]
    assert preceding["run_number"] == 812
    assert preceding["relation"] == "completed_before"
    assert preceding["minutes_before"] in (3, 4)  # depends on the second the test runs in
    assert "after deployment #812 (a1b2c3d) finished (succeeded)" in preceding["summary"]
    assert preceding["url"].endswith("/812")

    listed = client.get("/api/applications/orders-api/incidents").json()["items"][0]
    assert listed["preceding_deployment"]["id"] == preceding["id"]


def test_an_incident_with_no_nearby_deployment_says_nothing(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    github.runs = [_recent_run(812, 300)]  # finished five hours ago
    _sync(client, app)
    _checks(app, "orders-api", ok(), http(500), http(500))
    application = client.get("/api/applications/orders-api").json()
    assert application["open_incident"]["preceding_deployment"] is None


def test_another_applications_deployment_is_not_related(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    _register(client, "billing-api")
    github.runs = [_recent_run(812, 4)]
    _sync(client, app)
    _checks(app, "billing-api", ok(), http(500), http(500))
    application = client.get("/api/applications/billing-api").json()
    assert application["open_incident"]["preceding_deployment"] is None


# --- fleet-wide incidents ---------------------------------------------------------------------


def test_incidents_across_applications(client: TestClient, app: FastAPI) -> None:
    _register(client, "orders-api")
    _register(client, "billing-api")
    now = utc_now()
    _checks(app, "orders-api", ok(), http(500), http(500), ok(), ok(), end=now)
    _checks(app, "billing-api", ok(), http(503), http(503), end=now + timedelta(seconds=30))

    every = client.get("/api/incidents").json()["items"]
    assert [i["application_slug"] for i in every] == ["billing-api", "orders-api"]
    assert every[0]["application_name"] == "Billing Api"
    open_only = client.get("/api/incidents", params={"status": "open"}).json()["items"]
    assert [i["application_slug"] for i in open_only] == ["billing-api"]
    closed = client.get("/api/incidents", params={"status": "closed"}).json()["items"]
    assert [i["application_slug"] for i in closed] == ["orders-api"]
    one = client.get("/api/incidents", params={"application": "orders-api"}).json()["items"]
    assert len(one) == 1
    assert client.get("/api/incidents", params={"application": "nope"}).status_code == 404
    assert client.get("/api/incidents", params={"status": "maybe"}).status_code == 422


def test_incident_paging(client: TestClient, app: FastAPI) -> None:
    _register(client)
    pattern = [ok()] + [http(500), http(500), ok(), ok()] * 3
    _checks(app, "orders-api", *pattern)
    first = client.get("/api/incidents", params={"limit": 2}).json()
    assert len(first["items"]) == 2
    rest = client.get("/api/incidents", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert len(rest["items"]) == 1
    assert rest["next_cursor"] is None


# --- overview ---------------------------------------------------------------------------------


def test_an_empty_installation_has_an_honest_overview(client: TestClient) -> None:
    body = client.get("/api/overview").json()
    assert body["counts"] == {
        "total": 0,
        "healthy": 0,
        "degraded": 0,
        "unavailable": 0,
        "stale": 0,
        "pending": 0,
        "paused": 0,
    }
    assert body["needs_attention"] == []
    assert body["fleet"] == []
    assert body["recent_changes"] == []
    assert body["health_window_hours"] == 24


def test_overview_orders_problems_worst_first_and_counts_every_status(
    client: TestClient, app: FastAPI
) -> None:
    for slug in ("ok-api", "degraded-api", "down-api", "stale-api", "new-api", "paused-api"):
        _register(client, slug)
    client.post("/api/applications/paused-api/pause")
    _checks(app, "ok-api", ok())
    _checks(app, "degraded-api", ok(), http(500), http(500))
    _checks(app, "down-api", ok(), http(503), http(503))
    _checks(app, "stale-api", ok(), end=utc_now() - timedelta(minutes=30))

    body = client.get("/api/overview").json()
    assert body["counts"] == {
        "total": 6,
        "healthy": 1,
        "degraded": 1,
        "unavailable": 1,
        "stale": 1,
        "pending": 1,
        "paused": 1,
    }
    assert [e["application"]["slug"] for e in body["needs_attention"]] == [
        "down-api",
        "degraded-api",
        "stale-api",
    ]
    assert body["needs_attention"][0]["application"]["open_incident"]["worst_state"] == (
        "unavailable"
    )
    assert len(body["fleet"]) == 6
    changes = [event["type"] for event in body["recent_changes"]]
    assert sorted(changes) == ["health.degraded", "health.unavailable"]


def test_overview_reports_health_and_latest_deployment_per_application(
    client: TestClient, app: FastAPI, github: FakeGitHub
) -> None:
    _with_source(client)
    with session_scope(app.state.session_factory) as db:
        make_application(db, "other-api")
    github.runs = [_recent_run(811, 60), _recent_run(812, 10)]
    _sync(client, app)
    _checks(app, "orders-api", *[ok(duration_ms=100 + i) for i in range(12)])

    body = client.get("/api/overview").json()
    entries = {e["application"]["slug"]: e for e in body["fleet"]}
    orders = entries["orders-api"]
    assert orders["latest_deployment"]["run_number"] == 812
    assert orders["availability_24h"]["checks"] == 12
    assert orders["p95_ms_24h"] == 111
    assert "not uptime" in body["definitions"]["availability"]
    assert [d["run_number"] for d in body["recent_deployments"]] == [812, 811]
    assert [e["type"] for e in body["recent_changes"]] == [
        "deployment.succeeded",
        "deployment.succeeded",
    ]
    other = entries["other-api"]
    assert other["latest_deployment"] is None
    assert other["p95_ms_24h"] is None
    assert other["availability_24h"]["availability"] is None


def test_overview_lists_recently_closed_incidents(client: TestClient, app: FastAPI) -> None:
    _register(client)
    _checks(app, "orders-api", ok(), http(500), http(500), ok(), ok())
    body = client.get("/api/overview").json()
    assert body["needs_attention"] == []
    assert [i["application_slug"] for i in body["recent_incidents"]] == ["orders-api"]
    assert body["recent_incidents"][0]["closed_at"] is not None
