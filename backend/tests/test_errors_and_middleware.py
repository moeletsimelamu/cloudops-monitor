from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloudops.config import Settings
from cloudops.main import create_app


@pytest.fixture
def app_with_failing_route(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/api/test/boom")
    def boom() -> None:
        raise RuntimeError("connect failed: password=hunter2")  # must never reach the client

    return app


@pytest.fixture
def failing_client(app_with_failing_route: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_failing_route, raise_server_exceptions=False) as test_client:
        yield test_client


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method(client: TestClient) -> None:
    response = client.post("/api/health/live")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_validation_errors_list_fields_without_echoing_input(client: TestClient) -> None:
    rejected = "x" * 600
    response = client.post("/api/auth/session", json={"token": rejected})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"][0]["location"] == ["body", "token"]
    assert rejected not in response.text


def test_unhandled_errors_are_generic_and_logged(
    failing_client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    response = failing_client.get("/api/test/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    logged = capsys.readouterr().out
    assert "Unhandled error during GET /api/test/boom" in logged
    assert error["request_id"] in logged
    assert "hunter2" not in logged  # the redaction filter masks password=... in exceptions


def test_request_id_is_echoed_when_valid_and_replaced_when_not(client: TestClient) -> None:
    assert (
        client.get("/api/health/live", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"]
        == "abc-123"
    )
    replaced = client.get("/api/health/live", headers={"X-Request-ID": "bad id\x7f"})
    assert replaced.headers["X-Request-ID"] != "bad id\x7f"
    assert len(replaced.headers["X-Request-ID"]) == 32


def test_security_headers_on_api_responses(client: TestClient) -> None:
    headers = client.get("/api/health/live").headers
    assert headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"


def test_unknown_host_is_refused(client: TestClient) -> None:
    response = client.get("/api/health/live", headers={"Host": "attacker.example"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_host"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_writes_are_refused(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/api/auth/session", json={"token": "x"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cross_origin_request"


def test_same_origin_and_non_browser_writes_pass_the_guard(client: TestClient) -> None:
    same_origin = client.post(
        "/api/auth/session", json={"token": "wrong"}, headers={"Origin": "http://testserver"}
    )
    no_origin = client.post("/api/auth/session", json={"token": "wrong"})
    # Both reach the endpoint (and fail authentication there), rather than being refused.
    assert same_origin.status_code == 401
    assert no_origin.status_code == 401


def test_cross_site_reads_are_not_blocked(client: TestClient) -> None:
    response = client.get("/api/health/live", headers={"Origin": "https://elsewhere.example"})
    assert response.status_code == 200
