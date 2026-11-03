"""Log ingestion, redaction, querying and request metrics through the HTTP API."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from cloudops.db.models import LogEntry, OperationalEvent
from cloudops.db.session import session_scope
from cloudops.db.types import utc_now
from cloudops.main import create_app
from tests.conftest import OPERATOR_TOKEN, make_settings, operator_headers

AUTH = operator_headers()
INGEST = "/api/ingest/logs"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    settings = make_settings(tmp_path / "data", allowed_targets=["localhost:8310"])
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _register(client: TestClient, slug: str = "orders-api") -> str:
    """Register an application and return a new ingest key for it."""
    response = client.post(
        "/api/applications",
        headers=AUTH,
        json={
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "environment": "demo",
            "check_url": "http://localhost:8310/health",
        },
    )
    assert response.status_code == 201, response.text
    created = client.post(f"/api/applications/{slug}/ingest-key", headers=AUTH)
    assert created.status_code == 201
    return str(created.json()["ingest_key"])


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": utc_now().isoformat(),
        "severity": "info",
        "message": "order accepted",
    }
    entry.update(overrides)
    return entry


def _ship(client: TestClient, key: str, *entries: Any) -> dict[str, Any]:
    response = client.post(
        INGEST, headers={"Authorization": f"Bearer {key}"}, json={"entries": list(entries)}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _query(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/logs", headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


# --- ingest keys ------------------------------------------------------------------------------


def test_keys_are_shown_once_and_stored_hashed(client: TestClient, app: FastAPI) -> None:
    key = _register(client)
    assert key.startswith("cok_")
    assert len(key) > 40
    application = client.get("/api/applications/orders-api", headers=AUTH)
    assert application.json()["has_ingest_key"] is True
    assert key not in application.text
    with session_scope(app.state.session_factory) as db:
        events = list(db.scalars(select(OperationalEvent)))
    assert all(key not in str(e.details) and key not in e.summary for e in events)
    assert events[-1].type == "monitoring.ingest_key_created"


def test_rotating_replaces_the_key_and_revoking_removes_it(client: TestClient) -> None:
    old = _register(client)
    new = client.post("/api/applications/orders-api/ingest-key", headers=AUTH).json()["ingest_key"]
    assert new != old
    headers = {"Authorization": f"Bearer {old}"}
    assert client.post(INGEST, headers=headers, json={"entries": [_entry()]}).status_code == 401
    assert _ship(client, new, _entry())["accepted"] == 1

    assert client.delete("/api/applications/orders-api/ingest-key", headers=AUTH).status_code == 204
    headers = {"Authorization": f"Bearer {new}"}
    assert client.post(INGEST, headers=headers, json={"entries": [_entry()]}).status_code == 401


def test_key_management_requires_the_operator(client: TestClient) -> None:
    _register(client)
    assert client.post("/api/applications/orders-api/ingest-key").status_code == 401
    assert client.delete("/api/applications/orders-api/ingest-key").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer cok_not-a-real-key-000000000000"},
        {"Authorization": "Bearer something-else"},
        {"Authorization": f"Bearer {OPERATOR_TOKEN}"},  # the operator token is not an ingest key
    ],
)
def test_ingest_needs_a_valid_key(client: TestClient, headers: dict[str, str]) -> None:
    _register(client)
    response = client.post(INGEST, headers=headers, json={"entries": [_entry()]})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_ingest_key"


def test_a_key_only_writes_to_its_own_application(client: TestClient) -> None:
    orders_key = _register(client, "orders-api")
    _register(client, "billing-api")
    _ship(client, orders_key, _entry(message="from orders"))
    assert [i["message"] for i in _query(client, application="orders-api")] == ["from orders"]
    assert _query(client, application="billing-api") == []


def test_an_ingest_key_cannot_read_anything(client: TestClient) -> None:
    key = _register(client)
    headers = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/logs", headers=headers).status_code == 401
    assert client.get("/api/applications", headers=headers).status_code == 401


# --- validation and limits --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "location"),
    [
        ({"severity": "loud"}, "severity"),
        ({"message": ""}, "message"),
        ({"message": "x" * 4097}, "message"),
        ({"timestamp": "2026-10-27T12:00:00"}, "timestamp"),  # no time zone
        ({"timestamp": "yesterday"}, "timestamp"),
        ({"event": "has spaces"}, "event"),
        ({"correlation_id": "x" * 101}, "correlation_id"),
        ({"environment": "PROD!"}, "environment"),
        ({"attributes": {"nested": {"a": 1}}}, "attributes"),
        ({"attributes": {f"k{i}": 1 for i in range(33)}}, "attributes"),
        ({"attributes": {"k" * 65: 1}}, "attributes"),
        ({"attributes": {"long": "x" * 1025}}, "attributes"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_entries_are_reported_and_the_rest_kept(
    client: TestClient, overrides: dict[str, Any], location: str
) -> None:
    key = _register(client)
    result = _ship(client, key, _entry(message="good one"), _entry(**overrides))
    assert result["accepted"] == 1
    assert [r["index"] for r in result["rejected"]] == [1]
    assert result["rejected"][0]["errors"][0]["location"][0] == location
    assert [i["message"] for i in _query(client)] == ["good one"]


def test_rejections_do_not_repeat_the_input(client: TestClient) -> None:
    key = _register(client)
    secret = "password=hunter2-in-a-bad-entry"
    response = client.post(
        INGEST,
        headers={"Authorization": f"Bearer {key}"},
        json={"entries": [_entry(severity=secret), "not even an object"]},
    )
    assert response.status_code == 200
    assert "hunter2" not in response.text
    assert [r["index"] for r in response.json()["rejected"]] == [0, 1]


@pytest.mark.parametrize(
    "offset",
    [timedelta(hours=25), timedelta(minutes=-6)],
    ids=["too old", "in the future"],
)
def test_timestamps_outside_the_window_are_rejected(client: TestClient, offset: timedelta) -> None:
    key = _register(client)
    result = _ship(client, key, _entry(timestamp=(utc_now() - offset).isoformat()))
    assert result["accepted"] == 0
    assert "sender's clock" in result["rejected"][0]["errors"][0]["message"]


def test_batch_size_limits(client: TestClient) -> None:
    key = _register(client)
    headers = {"Authorization": f"Bearer {key}"}
    assert client.post(INGEST, headers=headers, json={"entries": []}).status_code == 422
    too_many = client.post(INGEST, headers=headers, json={"entries": [_entry()] * 501})
    assert too_many.status_code == 422
    assert client.post(INGEST, headers=headers, json={"nope": []}).status_code == 422


def test_oversized_bodies_are_refused(client: TestClient) -> None:
    key = _register(client)
    response = client.post(
        INGEST,
        headers={"Authorization": f"Bearer {key}"},
        content=b'{"entries": ["' + b"x" * (256 * 1024) + b'"]}',
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_oversized_bodies_without_a_length_header_are_refused(client: TestClient) -> None:
    key = _register(client)

    def chunks() -> Iterator[bytes]:
        yield b'{"entries": ["'
        for _ in range(300):
            yield b"x" * 1024
        yield b'"]}'

    response = client.post(INGEST, headers={"Authorization": f"Bearer {key}"}, content=chunks())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_entries_per_minute_are_limited(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "limited", ingest_max_entries_per_minute=100)
    settings.allowed_targets = ["localhost:8310"]
    with TestClient(create_app(settings)) as client:
        key = _register(client)
        headers = {"Authorization": f"Bearer {key}"}
        # Sent as one or two windows, depending on the clock: at most one batch can reset.
        statuses = [
            client.post(INGEST, headers=headers, json={"entries": [_entry()] * 80}).status_code
            for _ in range(3)
        ]
    assert 429 in statuses
    assert statuses[0] == 200


# --- redaction --------------------------------------------------------------------------------


def test_secrets_are_masked_before_storage(client: TestClient, app: FastAPI) -> None:
    key = _register(client)
    _ship(
        client,
        key,
        _entry(
            severity="error",
            message=f"login failed password=hunter2 with Bearer abcdefgh12345678 {OPERATOR_TOKEN}",
            attributes={
                "authorization": "Bearer abcdefgh12345678",
                "session_id": "s-123",
                "note": "token: tok_live_abc",
                "status_code": 500,
                "ingest": key,
            },
        ),
    )
    with session_scope(app.state.session_factory) as db:
        stored = db.scalars(select(LogEntry)).one()
    raw = stored.message + str(stored.attributes)
    for secret in ("hunter2", "abcdefgh12345678", OPERATOR_TOKEN, "s-123", "tok_live_abc", key):
        assert secret not in raw
    assert stored.attributes is not None
    assert stored.attributes["status_code"] == 500  # ordinary values are untouched
    assert stored.message.startswith("login failed password=[REDACTED]")


# --- querying ---------------------------------------------------------------------------------


@pytest.fixture
def seeded(client: TestClient) -> datetime:
    orders = _register(client, "orders-api")
    billing = _register(client, "billing-api")
    now = utc_now()

    def at(minutes: int) -> str:
        return (now - timedelta(minutes=minutes)).isoformat()

    _ship(
        client,
        orders,
        _entry(timestamp=at(50), severity="debug", message="cache miss", event="cache.miss"),
        _entry(
            timestamp=at(40),
            severity="info",
            message="order 100% accepted",
            event="order.accepted",
            correlation_id="req-1",
        ),
        _entry(
            timestamp=at(30),
            severity="ERROR",
            message="order database timeout",
            event="order.failed",
            correlation_id="req-2",
        ),
    )
    _ship(
        client,
        billing,
        _entry(timestamp=at(20), severity="warning", message="invoice retry", event="invoice"),
        _entry(timestamp=at(10), severity="critical", message="payments down", event="invoice"),
    )
    return now


def _messages(client: TestClient, **params: Any) -> list[str]:
    return [item["message"] for item in _query(client, **params)]


def test_logs_are_newest_first_with_context(client: TestClient, seeded: datetime) -> None:
    items = _query(client)
    assert [i["message"] for i in items][:2] == ["payments down", "invoice retry"]
    assert items[0]["severity"] == "critical"
    assert items[0]["application_slug"] == "billing-api"
    assert items[0]["application_name"] == "Billing Api"
    assert items[0]["environment"] == "demo"  # from the application when not sent


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"application": "orders-api"},
            ["order database timeout", "order 100% accepted", "cache miss"],
        ),
        ({"min_severity": "error"}, ["payments down", "order database timeout"]),
        ({"event": "invoice"}, ["payments down", "invoice retry"]),
        ({"correlation_id": "req-2"}, ["order database timeout"]),
        ({"q": "TIMEOUT"}, ["order database timeout"]),
        ({"q": "100%"}, ["order 100% accepted"]),  # % is literal, not a wildcard
        ({"q": "%"}, ["order 100% accepted"]),
        ({"q": "order_"}, []),  # _ is literal too
        ({"application": "orders-api", "min_severity": "warning"}, ["order database timeout"]),
    ],
)
def test_log_filters(
    client: TestClient, seeded: datetime, params: dict[str, str], expected: list[str]
) -> None:
    assert _messages(client, **params) == expected


def test_log_time_range(client: TestClient, seeded: datetime) -> None:
    window = {
        "from": (seeded - timedelta(minutes=35)).isoformat(),
        "to": (seeded - timedelta(minutes=15)).isoformat(),
    }
    assert _messages(client, **window) == ["invoice retry", "order database timeout"]


def test_log_paging(client: TestClient, seeded: datetime) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(4):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/logs", headers=AUTH, params=params).json()
        seen += [i["message"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 5
    assert cursor is None


def test_late_arriving_entries_sort_by_their_own_time(client: TestClient) -> None:
    key = _register(client)
    now = utc_now()
    _ship(client, key, _entry(timestamp=now.isoformat(), message="newer, sent first"))
    _ship(
        client,
        key,
        _entry(timestamp=(now - timedelta(minutes=5)).isoformat(), message="older, sent later"),
    )
    assert _messages(client) == ["newer, sent first", "older, sent later"]


@pytest.mark.parametrize(
    ("params", "code", "status"),
    [
        (
            {"from": "2026-10-01T00:00:00Z", "to": "2026-10-20T00:00:00Z"},
            "invalid_time_range",
            422,
        ),
        ({"min_severity": "loud"}, "invalid_request", 422),
        ({"q": "x" * 201}, "invalid_request", 422),
        ({"application": "nope"}, "not_found", 404),
        ({"cursor": "garbage"}, "invalid_cursor", 422),
    ],
)
def test_invalid_log_queries(
    client: TestClient, params: dict[str, str], code: str, status: int
) -> None:
    response = client.get("/api/logs", headers=AUTH, params=params)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_log_queries_require_the_operator(client: TestClient) -> None:
    assert client.get("/api/logs").status_code == 401


# --- request metrics --------------------------------------------------------------------------


def test_request_metrics_count_requests_and_server_errors(client: TestClient) -> None:
    key = _register(client)
    now = utc_now()

    def request_log(minutes: int, status: Any) -> dict[str, Any]:
        return _entry(
            timestamp=(now - timedelta(minutes=minutes)).isoformat(),
            event="http.request",
            message="GET /api/orders",
            attributes={"status_code": status, "path": "/api/orders"},
        )

    _ship(
        client,
        key,
        request_log(30, 200),
        request_log(30, 200),
        request_log(5, 500),
        request_log(5, 503),
        request_log(5, 404),  # a client error is a request, not a server failure
        request_log(5, "not a number"),
        _entry(message="an unrelated log line", attributes={"status_code": 500}),
    )
    body = client.get(
        "/api/applications/orders-api/request-metrics", headers=AUTH, params={"window": "1h"}
    ).json()
    assert body["has_data"] is True
    assert body["requests"] == 5
    assert body["errors"] == 2
    assert body["bucket_seconds"] == 60
    assert len(body["buckets"]) == 60
    assert sum(b["requests"] for b in body["buckets"]) == 5
    assert sum(b["errors"] for b in body["buckets"]) == 2
    assert "http.request" in body["source"]


def test_request_metrics_are_empty_without_request_logs(client: TestClient) -> None:
    _register(client)
    body = client.get("/api/applications/orders-api/request-metrics", headers=AUTH).json()
    assert body["has_data"] is False
    assert body["requests"] == 0
    assert body["window"] == "24h"
