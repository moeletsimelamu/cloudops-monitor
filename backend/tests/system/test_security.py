"""Security behaviour that only a running server shows (Phase 8): what ``cloudops serve`` tells
uvicorn about proxies and headers, the token rotation that ends sessions across a restart, and a
request body too large to accept."""

import http.client
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.system.harness import TOKEN, Api, CloudOpsProcess

ROTATED = "system-test-rotated-token-0123456789-abcdef"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[CloudOpsProcess]:
    process = CloudOpsProcess(tmp_path)
    try:
        yield process
    finally:
        process.close()


def _sign_in(api: Api, token: str = TOKEN, **headers: str) -> tuple[int, str]:
    reply = api.call("POST", "/api/auth/session", {"token": token}, auth=False, headers=headers)
    return reply.status, reply.headers.get("set-cookie", "")


def _session_cookie(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0]  # "cloudops_session=..."


def test_forwarded_headers_are_ignored_and_the_server_is_not_named(
    server: CloudOpsProcess,
) -> None:
    server.start()
    api = server.api

    status, cookie = _sign_in(api, **{"X-Forwarded-Proto": "https"})
    assert status == 201
    assert "Secure" not in cookie  # a client cannot claim HTTPS
    assert "server" not in api.call("GET", "/api/health/live", auth=False).headers

    # Without a trusted proxy, a client cannot claim a new address for each guess either.
    for n in range(5):
        assert _sign_in(api, "guess", **{"X-Forwarded-For": f"203.0.113.{n}"})[0] == 401
    assert _sign_in(api, **{"X-Forwarded-For": "198.51.100.1"})[0] == 429


def test_a_trusted_proxy_is_believed(tmp_path: Path) -> None:
    server = CloudOpsProcess(tmp_path, CLOUDOPS_TRUSTED_PROXIES="127.0.0.1")
    try:
        server.start()
        api = server.api
        status, cookie = _sign_in(api, **{"X-Forwarded-Proto": "https"})
        assert status == 201
        assert "Secure" in cookie

        attacker = {"X-Forwarded-For": "203.0.113.7"}
        for _ in range(5):
            assert _sign_in(api, "guess", **attacker)[0] == 401
        assert _sign_in(api, **attacker)[0] == 429
        # The operator, behind the same proxy, is not locked out by someone else's guesses.
        assert _sign_in(api, **{"X-Forwarded-For": "198.51.100.20"})[0] == 201
    finally:
        server.close()


def test_rotating_the_operator_token_ends_sessions_across_a_restart(
    server: CloudOpsProcess,
) -> None:
    server.start()
    status, set_cookie = _sign_in(server.api)
    assert status == 201
    cookie = {"Cookie": _session_cookie(set_cookie)}
    assert server.api.call("GET", "/api/system/config", auth=False, headers=cookie).status == 200

    server.stop()
    server.extra_env["CLOUDOPS_OPERATOR_TOKEN"] = ROTATED
    server.start()
    api = server.api
    assert api.call("GET", "/api/system/config", auth=False, headers=cookie).status == 401
    assert api.call("GET", "/api/system/config").status == 401  # the old token as a bearer
    assert api.call("GET", "/api/system/config", bearer=ROTATED).status == 200
    assert _sign_in(api, ROTATED)[0] == 201


def test_an_oversized_request_is_refused_before_it_is_read(server: CloudOpsProcess) -> None:
    server.start()
    # Only the headers are sent: the answer comes from the declared length, without waiting for
    # (or holding) 8 MiB of body. A client still uploading sees the connection close.
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        connection.putrequest("POST", "/api/auth/session")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(8 * 1024 * 1024))
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read())
    finally:
        connection.close()
    assert response.status == 413
    assert body["error"]["code"] == "payload_too_large"
    assert server.api.ready_status() == 200  # and the server carries on
