from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cloudops_sandbox.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SANDBOX_SLOW_SECONDS", "0.2")
    monkeypatch.setenv("SANDBOX_VERSION", "1.2.3")
    monkeypatch.delenv("SANDBOX_CONTROL_TOKEN", raising=False)
    with TestClient(create_app()) as test_client:
        yield test_client


def _set(client: TestClient, mode: str) -> None:
    assert client.put("/control/mode", json={"mode": mode}).status_code == 200


def test_starts_healthy(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "pass", "version": "1.2.3"}
    assert client.get("/api/orders").status_code == 200
    assert client.get("/control").json()["mode"] == "healthy"


@pytest.mark.parametrize(
    ("mode", "health_status", "body_status", "orders_status"),
    [
        ("degraded", 200, "warn", 200),
        ("errors", 500, "fail", 500),
        ("unavailable", 503, "fail", 500),
        ("slow", 200, "pass", 200),
    ],
)
def test_modes(
    client: TestClient, mode: str, health_status: int, body_status: str, orders_status: int
) -> None:
    _set(client, mode)
    response = client.get("/health")
    assert response.status_code == health_status
    assert response.json()["status"] == body_status
    assert client.get("/api/orders").status_code == orders_status


def test_flaky_mode_fails_some_requests(client: TestClient) -> None:
    _set(client, "flaky")
    codes = {client.get("/health").status_code for _ in range(60)}
    assert codes == {200, 500}


def test_unknown_mode_is_rejected(client: TestClient) -> None:
    assert client.put("/control/mode", json={"mode": "on-fire"}).status_code == 422


def test_control_token_is_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_CONTROL_TOKEN", "sandbox-test-token")
    with TestClient(create_app()) as client:
        assert client.put("/control/mode", json={"mode": "errors"}).status_code == 401
        body = {"mode": "errors"}
        wrong = client.put("/control/mode", json=body, headers={"X-Sandbox-Token": "nope"})
        assert wrong.status_code == 401
        right_token = {"X-Sandbox-Token": "sandbox-test-token"}
        assert client.put("/control/mode", json=body, headers=right_token).status_code == 200
