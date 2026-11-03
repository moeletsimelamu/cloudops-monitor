"""The application registry API, including "check now" against a real local HTTP server."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from cloudops.db.models import Application, OperationalEvent
from cloudops.db.session import session_scope
from cloudops.db.types import utc_now
from cloudops.main import create_app
from cloudops.monitoring.recorder import record_check
from tests.conftest import make_settings, operator_headers
from tests.factories import http, ok
from tests.servers import ServerProcess

AUTH = operator_headers()


@pytest.fixture(scope="module")
def server() -> Iterator[ServerProcess]:
    with ServerProcess() as running:
        yield running


@pytest.fixture
def health(server: ServerProcess) -> Iterator[ServerProcess]:
    """For tests that switch the target's health; put back afterwards."""
    yield server
    server.set_health()


@pytest.fixture
def app(tmp_path: Path, server: ServerProcess) -> FastAPI:
    settings = make_settings(
        tmp_path / "data", allowed_targets=[f"127.0.0.1:{server.port}", "localhost:8310"]
    )
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, headers=AUTH) as test_client:
        yield test_client


def _body(server: ServerProcess, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "slug": "orders-api",
        "name": "Orders API",
        "description": "Order intake service",
        "environment": "demo",
        "check_url": server.url("/health"),
        "interval_seconds": 60,
        "timeout_seconds": 5,
    }
    body.update(overrides)
    return body


def _events(app: FastAPI) -> list[OperationalEvent]:
    with session_scope(app.state.session_factory) as db:
        return list(db.scalars(select(OperationalEvent).order_by(OperationalEvent.id)))


def _register(client: TestClient, server: ServerProcess, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/applications", json=_body(server, **overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- registry ---------------------------------------------------------------------------------


def test_every_route_requires_the_operator(app: FastAPI) -> None:
    with TestClient(app) as anonymous:
        for method, path in [
            ("GET", "/api/applications"),
            ("POST", "/api/applications"),
            ("GET", "/api/applications/x"),
            ("PATCH", "/api/applications/x"),
            ("DELETE", "/api/applications/x"),
            ("POST", "/api/applications/x/checks"),
            ("GET", "/api/applications/x/checks"),
            ("GET", "/api/applications/x/metrics"),
        ]:
            assert anonymous.request(method, path).status_code == 401, (method, path)


def test_register_list_and_get(client: TestClient, server: ServerProcess, app: FastAPI) -> None:
    created = _register(client, server)
    assert created["slug"] == "orders-api"
    assert created["status"] == "pending"
    assert created["health_state"] == "unknown"
    assert created["latest_check"] is None
    assert created["open_incident"] is None
    assert created["has_ingest_key"] is False
    assert created["next_check_at"] is not None  # checked straight away

    listing = client.get("/api/applications").json()
    assert [item["slug"] for item in listing["items"]] == ["orders-api"]
    assert client.get("/api/applications/orders-api").json()["name"] == "Orders API"
    assert [e.type for e in _events(app)] == ["application.registered"]


def test_unknown_application_is_404(client: TestClient) -> None:
    response = client.get("/api/applications/nope")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "There is no application 'nope'."


def test_duplicate_slug_is_a_conflict(client: TestClient, server: ServerProcess) -> None:
    _register(client, server)
    response = client.post("/api/applications", json=_body(server))
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"slug": "Orders API"}, "slug"),
        ({"slug": "a"}, "slug"),
        ({"environment": "Production!"}, "environment"),
        ({"interval_seconds": 5}, "interval_seconds"),
        ({"timeout_seconds": 20, "interval_seconds": 15}, "timeout_seconds must be shorter"),
        ({"timeout_seconds": 60}, "timeout_seconds"),
        ({"slow_threshold_ms": 5000, "timeout_seconds": 5}, "slow_threshold_ms must be shorter"),
        ({"github_repo": "moeletsimelamu/cloudops-monitor"}, "go together"),
        ({"deploy_workflow": "deploy.yml"}, "go together"),
        (
            {"github_repo": "a/b", "deploy_workflow": "deploy.yml", "deploy_branch": "a..b"},
            "not a valid branch name",
        ),
        ({"github_repo": "not a repo", "deploy_workflow": "deploy.yml"}, "github_repo"),
        ({"surprise": True}, "surprise"),
    ],
)
def test_invalid_registrations_are_rejected(
    client: TestClient, server: ServerProcess, overrides: dict[str, Any], fragment: str
) -> None:
    response = client.post("/api/applications", json=_body(server, **overrides))
    assert response.status_code == 422
    assert fragment in response.text


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://127.0.0.1:1/health", "not in CLOUDOPS_ALLOWED_TARGETS"),
        ("http://user:secret@localhost:8310/health", "Credentials in the URL"),
        ("gopher://localhost:8310/", "Only http:// and https://"),
        ("http://169.254.169.254/latest/meta-data/", "not in CLOUDOPS_ALLOWED_TARGETS"),
    ],
)
def test_targets_outside_the_policy_are_rejected(
    client: TestClient, server: ServerProcess, url: str, fragment: str
) -> None:
    response = client.post("/api/applications", json=_body(server, check_url=url))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "target_not_allowed"
    assert error["details"][0]["location"] == ["body", "check_url"]
    assert fragment in error["message"]
    assert "user:secret" not in response.text  # the rejected URL is not echoed back


def test_public_mode_checks_where_the_name_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def to_private(host: str, port: int) -> list[str]:
        return ["10.0.0.9"]

    monkeypatch.setattr("cloudops.api.applications.system_resolver", to_private)
    settings = make_settings(tmp_path / "data", target_policy="public")
    with TestClient(create_app(settings), headers=AUTH) as client:
        body = {
            "slug": "status-page",
            "name": "Status page",
            "environment": "production",
            "check_url": "https://status.example.com/health",
        }
        response = client.post("/api/applications", json=body)
    assert response.status_code == 422
    assert "resolves to 10.0.0.9" in response.json()["error"]["message"]


# --- updates, pause/resume, delete ----------------------------------------------------------


def test_update_changes_fields_and_records_which(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    response = client.patch(
        "/api/applications/orders-api", json={"name": "Orders", "failure_threshold": 3}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Orders"
    assert response.json()["failure_threshold"] == 3
    event = _events(app)[-1]
    assert event.type == "monitoring.configured"
    assert event.details == {"changed": ["failure_threshold", "name"]}

    # Sending the same values again changes nothing and records nothing.
    client.patch("/api/applications/orders-api", json={"name": "Orders"})
    assert len(_events(app)) == 2


def test_update_is_validated_against_the_stored_settings(
    client: TestClient, server: ServerProcess
) -> None:
    _register(client, server, interval_seconds=60, timeout_seconds=20)
    response = client.patch("/api/applications/orders-api", json={"interval_seconds": 15})
    assert response.status_code == 422
    assert "timeout_seconds must be shorter" in response.json()["error"]["message"]
    assert client.get("/api/applications/orders-api").json()["interval_seconds"] == 60


def test_update_cannot_move_the_target_outside_the_policy(
    client: TestClient, server: ServerProcess
) -> None:
    _register(client, server)
    response = client.patch(
        "/api/applications/orders-api", json={"check_url": "http://10.1.1.1:8080/health"}
    )
    assert response.status_code == 422
    assert client.get("/api/applications/orders-api").json()["check_url"] == server.url("/health")


def test_slug_cannot_change(client: TestClient, server: ServerProcess) -> None:
    _register(client, server)
    response = client.patch("/api/applications/orders-api", json={"slug": "other"})
    assert response.status_code == 422


def test_pause_and_resume(client: TestClient, server: ServerProcess, app: FastAPI) -> None:
    _register(client, server)
    paused = client.post("/api/applications/orders-api/pause").json()
    assert paused["status"] == "paused"
    assert paused["monitoring_enabled"] is False
    assert paused["next_check_at"] is None
    client.post("/api/applications/orders-api/pause")  # idempotent: no second event
    resumed = client.post("/api/applications/orders-api/resume").json()
    assert resumed["monitoring_enabled"] is True
    assert [e.type for e in _events(app)] == [
        "application.registered",
        "monitoring.paused",
        "monitoring.resumed",
    ]


def test_delete_removes_the_application_but_keeps_an_audit_event(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    assert client.delete("/api/applications/orders-api").status_code == 204
    assert client.get("/api/applications/orders-api").status_code == 404
    events = _events(app)
    assert [e.type for e in events] == ["application.registered", "application.removed"]
    assert all(e.application_id is None for e in events)
    assert events[-1].details == {"slug": "orders-api", "name": "Orders API"}


# --- check now --------------------------------------------------------------------------------


def test_check_now_records_a_real_check(
    client: TestClient, server: ServerProcess, health: ServerProcess
) -> None:
    _register(client, server)
    response = client.post("/api/applications/orders-api/checks")
    assert response.status_code == 201
    check = response.json()
    assert (check["signal"], check["http_status"], check["state_after"]) == ("ok", 200, "healthy")
    assert check["resolved_ip"] == "127.0.0.1"
    application = client.get("/api/applications/orders-api").json()
    assert application["status"] == "healthy"
    assert application["latest_check"]["id"] == check["id"]

    again = client.post("/api/applications/orders-api/checks")
    assert again.status_code == 429
    assert again.json()["error"]["code"] == "too_many_checks"
    assert int(again.headers["Retry-After"]) >= 1


def test_check_now_detects_a_failure_and_opens_an_incident(
    client: TestClient, server: ServerProcess, health: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    client.post("/api/applications/orders-api/checks")
    health.set_health(500)
    for _ in range(2):
        app.state.manual_checks.clear()  # skip the 5-second "check now" spacing in tests
        result = client.post("/api/applications/orders-api/checks").json()
        assert (result["signal"], result["reason"]) == ("warn", "server_error")
    application = client.get("/api/applications/orders-api").json()
    assert application["status"] == "degraded"
    assert application["last_failure_reason"] == "server_error"
    assert application["open_incident"]["worst_state"] == "degraded"
    assert _events(app)[-1].type == "health.degraded"
    assert _events(app)[-1].details["manual_check"] is True


def test_check_now_for_an_unknown_application(client: TestClient) -> None:
    assert client.post("/api/applications/nope/checks").status_code == 404


def test_check_now_while_a_check_runs_is_a_conflict(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    created = _register(client, server)
    app.state.check_runner.in_flight.add(created["id"])
    response = client.post("/api/applications/orders-api/checks")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "check_in_progress"


# --- history ----------------------------------------------------------------------------------


def _seed_checks(app: FastAPI, signals: str, *, every: timedelta, end: datetime) -> int:
    """Record checks ('o' ok, 'w' HTTP 500, 'f' HTTP 503) ending at ``end``. Returns the app id."""
    observations = {"o": ok(), "w": http(500), "f": http(503)}
    with session_scope(app.state.session_factory) as db:
        application = db.scalars(select(Application)).one()
        start = end - every * len(signals)
        for index, code in enumerate(signals):
            record_check(db, application.id, observations[code], start + every * index)
            db.flush()
        return application.id


def test_checks_are_paged_newest_first_and_filterable(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    _seed_checks(app, "oooww", every=timedelta(minutes=1), end=utc_now())
    first = client.get("/api/applications/orders-api/checks", params={"limit": 2}).json()
    assert [c["signal"] for c in first["items"]] == ["warn", "warn"]
    assert first["next_before"] is not None
    second = client.get(
        "/api/applications/orders-api/checks",
        params={"limit": 2, "before": first["next_before"]},
    ).json()
    assert [c["signal"] for c in second["items"]] == ["ok", "ok"]
    rest = client.get(
        "/api/applications/orders-api/checks",
        params={"limit": 2, "before": second["next_before"]},
    ).json()
    assert len(rest["items"]) == 1
    assert rest["next_before"] is None
    only_warn = client.get("/api/applications/orders-api/checks", params={"signal": "warn"}).json()
    assert len(only_warn["items"]) == 2


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"from": "2026-10-27T12:00:00Z", "to": "2026-10-27T11:00:00Z"}, "invalid_time_range"),
        ({"from": "2026-08-01T00:00:00Z", "to": "2026-10-27T00:00:00Z"}, "invalid_time_range"),
        ({"from": "2026-10-27T12:00:00"}, "invalid_request"),  # no time zone
        ({"limit": 0}, "invalid_request"),
        ({"limit": 500}, "invalid_request"),
        ({"signal": "maybe"}, "invalid_request"),
    ],
)
def test_history_parameters_are_validated(
    client: TestClient, server: ServerProcess, params: dict[str, Any], code: str
) -> None:
    _register(client, server)
    response = client.get("/api/applications/orders-api/checks", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_time_range_filters_checks(client: TestClient, server: ServerProcess, app: FastAPI) -> None:
    _register(client, server)
    end = utc_now()
    _seed_checks(app, "o" * 10, every=timedelta(hours=1), end=end)
    params = {
        "from": (end - timedelta(hours=3, minutes=30)).isoformat(),
        "to": end.isoformat(),
    }
    items = client.get("/api/applications/orders-api/checks", params=params).json()["items"]
    assert len(items) == 3


def test_metrics_report_availability_coverage_and_latency(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server, interval_seconds=60)
    with session_scope(app.state.session_factory) as db:
        db.scalars(select(Application)).one().created_at = utc_now() - timedelta(days=2)
    # One check a minute for the last hour, the newest 30 s ago: 54 ok, 6 failing with HTTP 503.
    _seed_checks(
        app, "o" * 54 + "f" * 6, every=timedelta(minutes=1), end=utc_now() + timedelta(seconds=30)
    )
    body = client.get("/api/applications/orders-api/metrics", params={"window": "1h"}).json()
    availability = body["availability"]
    assert availability["checks"] == 60
    assert availability["fail"] == 6
    assert availability["availability"] == pytest.approx(0.9)
    assert availability["coverage"] == pytest.approx(1.0, abs=0.02)
    assert body["p50_ms"] == 40
    assert body["bucket_seconds"] == 60
    assert len(body["buckets"]) == 60
    assert "not uptime" in body["definitions"]["availability"]


def test_metrics_say_when_there_is_too_little_data(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    _seed_checks(app, "ooo", every=timedelta(minutes=1), end=utc_now())
    body = client.get("/api/applications/orders-api/metrics").json()
    assert body["window"] == "24h"
    assert body["availability"]["availability"] is None
    assert body["availability"]["insufficient_reason"] == "Fewer than 10 checks in this window"


def test_metrics_window_is_validated(client: TestClient, server: ServerProcess) -> None:
    _register(client, server)
    response = client.get("/api/applications/orders-api/metrics", params={"window": "1y"})
    assert response.status_code == 422


def test_incidents_are_listed_newest_first(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    _seed_checks(app, "ooww" + "oo" + "ff", every=timedelta(minutes=1), end=utc_now())
    items = client.get("/api/applications/orders-api/incidents").json()["items"]
    assert [i["worst_state"] for i in items] == ["unavailable", "degraded"]
    assert items[0]["closed_at"] is None
    assert items[0]["duration_seconds"] is None
    assert items[1]["duration_seconds"] == 120  # degraded at the 4th check, healthy at the 6th


def test_an_old_observation_is_shown_as_stale(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server, interval_seconds=60)
    _seed_checks(app, "oo", every=timedelta(minutes=1), end=utc_now() - timedelta(minutes=10))
    application = client.get("/api/applications/orders-api").json()
    assert application["health_state"] == "healthy"
    assert application["status"] == "stale"
    assert application["stale_since"] is not None


def test_system_status_reports_worker_and_database(
    client: TestClient, server: ServerProcess, app: FastAPI
) -> None:
    _register(client, server)
    _seed_checks(app, "ww", every=timedelta(minutes=1), end=utc_now())
    body = client.get("/api/system/status").json()
    assert body["worker"]["enabled"] is False
    assert body["worker"]["healthy"] is False
    assert body["database"]["applications"] == 1
    assert body["database"]["health_checks"] == 2
    assert body["database"]["open_incidents"] == 1
    assert body["database"]["size_bytes"] > 0
    assert any("worker is disabled" in w for w in body["warnings"])
