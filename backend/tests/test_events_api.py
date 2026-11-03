from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloudops.db.session import session_scope
from cloudops.db.types import utc_now
from cloudops.events import record_event
from cloudops.main import create_app
from tests.conftest import make_settings, operator_headers
from tests.factories import make_application


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(make_settings(tmp_path / "data"))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, headers=operator_headers()) as test_client:
        yield test_client


@pytest.fixture
def seeded(client: TestClient, app: FastAPI) -> datetime:
    now = utc_now()
    with session_scope(app.state.session_factory) as db:
        orders = make_application(db, "orders-api")
        billing = make_application(db, "billing-api")
        rows = [
            (orders.id, "health.degraded", "warning", 50),
            (orders.id, "deployment.succeeded", "info", 40),
            (billing.id, "health.unavailable", "critical", 30),
            (billing.id, "deployments.sync_failed", "warning", 20),
            (None, "monitoring.worker_started", "info", 10),
        ]
        for application_id, event_type, severity, minutes in rows:
            record_event(
                db,
                type=event_type,
                severity=severity,  # type: ignore[arg-type]
                summary=f"{event_type} happened",
                occurred_at=now - timedelta(minutes=minutes),
                application_id=application_id,
                details={"minutes": minutes},
            )
    return now


def _types(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/events", params=params)  # type: ignore[arg-type]
    assert response.status_code == 200, response.text
    return [item["type"] for item in response.json()["items"]]


def test_events_require_the_operator(app: FastAPI, client: TestClient) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/events").status_code == 401


def test_events_are_newest_first_with_application_names(
    client: TestClient, seeded: datetime
) -> None:
    items = client.get("/api/events").json()["items"]
    assert [i["type"] for i in items][:2] == [
        "monitoring.worker_started",
        "deployments.sync_failed",
    ]
    assert items[0]["application_slug"] is None
    assert items[1]["application_slug"] == "billing-api"
    assert items[1]["application_name"] == "Billing Api"
    assert items[1]["details"] == {"minutes": 20}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"application": "orders-api"}, ["deployment.succeeded", "health.degraded"]),
        ({"category": "health"}, ["health.unavailable", "health.degraded"]),
        ({"category": "deployment"}, ["deployments.sync_failed", "deployment.succeeded"]),
        ({"type": "health.degraded"}, ["health.degraded"]),
        ({"min_severity": "critical"}, ["health.unavailable"]),
        (
            {"min_severity": "warning"},
            ["deployments.sync_failed", "health.unavailable", "health.degraded"],
        ),
        ({"application": "billing-api", "category": "health"}, ["health.unavailable"]),
    ],
)
def test_filters(
    client: TestClient, seeded: datetime, params: dict[str, str], expected: list[str]
) -> None:
    assert _types(client, **params) == expected


def test_time_range_filter(client: TestClient, seeded: datetime) -> None:
    window = {
        "from": (seeded - timedelta(minutes=35)).isoformat(),
        "to": (seeded - timedelta(minutes=15)).isoformat(),
    }
    assert _types(client, **window) == ["deployments.sync_failed", "health.unavailable"]


def test_paging_uses_time_order_even_when_ids_are_not_in_time_order(
    client: TestClient, app: FastAPI, seeded: datetime
) -> None:
    # A backfilled event: inserted last, but older than everything else.
    with session_scope(app.state.session_factory) as db:
        record_event(
            db,
            type="deployment.failed",
            severity="error",
            summary="old run first seen now",
            occurred_at=seeded - timedelta(minutes=90),
        )
    seen: list[str] = []
    cursor = None
    for _ in range(4):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/events", params=params).json()
        seen += [i["type"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen[-1] == "deployment.failed"
    assert len(seen) == len(set(seen)) == 6
    assert cursor is None


@pytest.mark.parametrize("cursor", ["not-a-cursor", "bm90fGFuaWQ=", "MjAyNi0xMC0yN3wx"])
def test_bad_cursor_is_rejected(client: TestClient, cursor: str) -> None:
    response = client.get("/api/events", params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


def test_unknown_application_is_404(client: TestClient) -> None:
    assert client.get("/api/events", params={"application": "nope"}).status_code == 404


def test_removed_applications_keep_their_events(
    client: TestClient, app: FastAPI, seeded: datetime
) -> None:
    with session_scope(app.state.session_factory) as db:
        record_event(
            db,
            type="application.removed",
            severity="info",
            summary="Old API removed from monitoring",
            occurred_at=datetime.now(UTC),
            details={"slug": "old-api", "name": "Old API"},
        )
    item = client.get("/api/events", params={"category": "application"}).json()["items"][0]
    assert item["application_slug"] is None
    assert item["details"]["slug"] == "old-api"
