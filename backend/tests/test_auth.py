from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from cloudops.auth import SESSION_COOKIE, FailedSignInLimiter, hash_secret
from cloudops.db.models import OperatorSession
from cloudops.db.types import utc_now
from cloudops.main import create_app
from tests.conftest import OPERATOR_TOKEN, make_settings, operator_headers

PROTECTED = "/api/system/config"


def _sign_in(client: TestClient, token: str = OPERATOR_TOKEN) -> object:
    return client.post("/api/auth/session", json={"token": token})


def test_protected_routes_require_authentication(client: TestClient) -> None:
    response = client.get(PROTECTED)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"


def test_session_status_when_signed_out(client: TestClient) -> None:
    assert client.get("/api/auth/session").json() == {
        "auth_mode": "token",
        "authenticated": False,
        "expires_at": None,
    }


def test_wrong_token_is_rejected(client: TestClient) -> None:
    response = client.post("/api/auth/session", json={"token": "not-the-token"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert SESSION_COOKIE not in response.cookies


def test_sign_in_sets_a_strict_http_only_cookie_and_grants_access(client: TestClient) -> None:
    response = client.post("/api/auth/session", json={"token": OPERATOR_TOKEN})
    assert response.status_code == 201
    assert response.json()["authenticated"] is True
    cookie_header = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert "Path=/api" in cookie_header
    assert "Max-Age=43200" in cookie_header
    assert "Secure" not in cookie_header  # plain HTTP in this test

    assert client.get(PROTECTED).status_code == 200
    status = client.get("/api/auth/session").json()
    assert status["authenticated"] is True
    assert status["expires_at"] is not None


def test_cookie_is_secure_over_https(settings: object) -> None:
    app = create_app(settings)  # type: ignore[arg-type]
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/auth/session", json={"token": OPERATOR_TOKEN})
    assert "Secure" in response.headers["set-cookie"]


def test_only_a_hash_of_the_session_is_stored(client: TestClient) -> None:
    response = client.post("/api/auth/session", json={"token": OPERATOR_TOKEN})
    raw = response.cookies[SESSION_COOKIE]
    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        stored = db.scalars(select(OperatorSession.token_hash)).one()
    assert stored == hash_secret(raw)
    assert stored != raw


def test_bearer_token_grants_access_and_a_wrong_one_does_not(client: TestClient) -> None:
    assert client.get(PROTECTED, headers=operator_headers()).status_code == 200
    wrong = client.get(PROTECTED, headers={"Authorization": "Bearer wrong-token"})
    assert wrong.status_code == 401


def test_sign_out_ends_the_session(client: TestClient) -> None:
    raw = client.post("/api/auth/session", json={"token": OPERATOR_TOKEN}).cookies[SESSION_COOKIE]
    response = client.delete("/api/auth/session")
    assert response.status_code == 204
    client.cookies.set(SESSION_COOKIE, raw, path="/api")  # replaying the old cookie fails
    assert client.get(PROTECTED).status_code == 401


def test_expired_sessions_are_refused(client: TestClient) -> None:
    client.post("/api/auth/session", json={"token": OPERATOR_TOKEN})
    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        db.execute(update(OperatorSession).values(expires_at=utc_now() - timedelta(seconds=1)))
        db.commit()
    assert client.get(PROTECTED).status_code == 401


def test_unknown_cookie_is_refused(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "made-up-session-id", path="/api")
    assert client.get(PROTECTED).status_code == 401


def test_repeated_failures_are_rate_limited_even_for_the_right_token(client: TestClient) -> None:
    for _ in range(5):
        assert client.post("/api/auth/session", json={"token": "guess"}).status_code == 401
    limited = client.post("/api/auth/session", json={"token": OPERATOR_TOKEN})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "too_many_attempts"
    assert 1 <= int(limited.headers["Retry-After"]) <= 61


def test_limiter_forgets_failures_after_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr("cloudops.auth.time.monotonic", lambda: clock[0])
    limiter = FailedSignInLimiter(max_failures=2, window_seconds=60)
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 61
    assert limiter.retry_after("b") == 0
    clock[0] += 60
    assert limiter.retry_after("a") == 0


@pytest.fixture
def open_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = make_settings(tmp_path / "data", auth_mode="disabled", operator_token=None)
    with TestClient(create_app(settings)) as client:
        yield client


def test_disabled_mode_allows_access_and_says_so(open_client: TestClient) -> None:
    assert open_client.get(PROTECTED).status_code == 200
    assert open_client.get("/api/auth/session").json() == {
        "auth_mode": "disabled",
        "authenticated": True,
        "expires_at": None,
    }
    response = open_client.post("/api/auth/session", json={"token": "anything"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "auth_disabled"
