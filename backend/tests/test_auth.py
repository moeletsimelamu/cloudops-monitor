from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from cloudops.auth import SESSION_COOKIE, FailedSignInLimiter, hash_secret, session_digest
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


def test_only_a_keyed_digest_of_the_session_is_stored(client: TestClient) -> None:
    response = client.post("/api/auth/session", json={"token": OPERATOR_TOKEN})
    raw = response.cookies[SESSION_COOKIE]
    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        stored = db.scalars(select(OperatorSession.token_hash)).one()
    assert stored == session_digest(raw, OPERATOR_TOKEN)
    assert stored not in (raw, hash_secret(raw), hash_secret(OPERATOR_TOKEN))


def test_rotating_the_operator_token_ends_every_session(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with TestClient(create_app(make_settings(data))) as before:
        raw = before.post("/api/auth/session", json={"token": OPERATOR_TOKEN}).cookies[
            SESSION_COOKIE
        ]
        assert before.get(PROTECTED).status_code == 200

    rotated = "rotated-operator-token-0123456789-abcdefgh"
    with TestClient(create_app(make_settings(data, operator_token=rotated))) as after:
        after.cookies.set(SESSION_COOKIE, raw, path="/api")
        assert after.get(PROTECTED).status_code == 401
        assert after.get("/api/auth/session").json()["authenticated"] is False
        assert after.get(PROTECTED, headers=operator_headers()).status_code == 401  # old token
        assert after.post("/api/auth/session", json={"token": rotated}).status_code == 201
        assert after.get(PROTECTED).status_code == 200


def test_signing_out_everywhere_ends_every_session(app: object) -> None:
    with TestClient(app) as laptop, TestClient(app) as phone:  # type: ignore[arg-type]
        _sign_in(laptop)
        _sign_in(phone)
        response = laptop.delete("/api/auth/sessions")
        assert response.status_code == 200
        assert response.json() == {"ended": 2}
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert phone.get(PROTECTED).status_code == 401
        laptop.cookies.clear()
        assert laptop.get(PROTECTED).status_code == 401
        # It is an operator action itself.
        assert laptop.delete("/api/auth/sessions").status_code == 401


def test_bearer_token_grants_access_and_a_wrong_one_does_not(client: TestClient) -> None:
    assert client.get(PROTECTED, headers=operator_headers()).status_code == 200
    wrong = client.get(PROTECTED, headers={"Authorization": "Bearer wrong-token"})
    assert wrong.status_code == 401


def test_wrong_bearer_tokens_share_the_sign_in_failure_limit(client: TestClient) -> None:
    for _ in range(5):
        guess = client.get(PROTECTED, headers={"Authorization": "Bearer guess"})
        assert guess.status_code == 401
    limited = client.get(PROTECTED, headers=operator_headers())
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "too_many_attempts"
    assert client.post("/api/auth/session", json={"token": OPERATOR_TOKEN}).status_code == 429


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


def _failed_sign_ins(client: TestClient, forwarded_for: str, count: int) -> int:
    status = 0
    for _ in range(count):
        status = client.post(
            "/api/auth/session",
            json={"token": "guess"},
            headers={"X-Forwarded-For": forwarded_for},
        ).status_code
    return status


def test_forwarded_headers_are_ignored_unless_the_proxy_is_trusted(settings: object) -> None:
    app = create_app(settings)  # type: ignore[arg-type]
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        # Claiming a new address for each guess does not escape the limit...
        for n in range(5):
            _failed_sign_ins(client, f"203.0.113.{n}", 1)
        assert _failed_sign_ins(client, "203.0.113.99", 1) == 429
    with TestClient(app, client=("127.0.0.2", 50000)) as client:
        # ...and claiming HTTPS does not make the cookie Secure on plain HTTP.
        response = client.post(
            "/api/auth/session",
            json={"token": OPERATOR_TOKEN},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert response.status_code == 201
        assert "Secure" not in response.headers["set-cookie"]


def test_behind_a_trusted_proxy_cookies_are_secure_and_limits_are_per_client(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path / "data", trusted_proxies=["10.0.0.0/24"])
    with TestClient(create_app(settings), client=("10.0.0.2", 50000)) as proxy:
        assert _failed_sign_ins(proxy, "203.0.113.7", 5) == 401
        assert _failed_sign_ins(proxy, "203.0.113.7", 1) == 429
        # The operator, at another address behind the same proxy, is not locked out.
        response = proxy.post(
            "/api/auth/session",
            json={"token": OPERATOR_TOKEN},
            headers={"X-Forwarded-For": "198.51.100.20", "X-Forwarded-Proto": "https"},
        )
        assert response.status_code == 201
        assert "Secure" in response.headers["set-cookie"]
