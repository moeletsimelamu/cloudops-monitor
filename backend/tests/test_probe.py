"""The probe against real sockets: local HTTP servers, raw TCP servers and closed ports."""

import asyncio
import contextlib
import gc
import socket
import time
from collections.abc import Iterator

import anyio
import pytest

from cloudops.domain.classification import ProbeError
from cloudops.monitoring.probe import MAX_BODY_BYTES, probe
from cloudops.monitoring.target_policy import TargetPolicy
from tests.servers import ServerProcess, closed_port, raw_tcp_server

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    """One event loop for the module: production has one long-running loop too."""
    return "asyncio"


@pytest.fixture(scope="module")
def server() -> Iterator[ServerProcess]:
    with ServerProcess() as running:
        yield running


def _policy(*ports: int) -> TargetPolicy:
    return TargetPolicy("allowlist", [f"127.0.0.1:{port}" for port in ports])


async def test_successful_check_records_status_body_timing_and_address(
    server: ServerProcess,
) -> None:
    result = await probe(server.url("/ok"), timeout_seconds=5, policy=_policy(server.port))
    assert result.error is None
    assert result.status_code == 200
    assert b'"pass"' in result.body
    assert result.duration_ms is not None
    assert 0 <= result.duration_ms < 5000
    assert result.resolved_ip == "127.0.0.1"


@pytest.mark.parametrize("code", [404, 429, 500, 503])
async def test_http_error_statuses_are_observations_not_errors(
    server: ServerProcess, code: int
) -> None:
    result = await probe(
        server.url(f"/status/{code}"), timeout_seconds=5, policy=_policy(server.port)
    )
    assert result.error is None
    assert result.status_code == code


async def test_redirects_are_not_followed(server: ServerProcess) -> None:
    result = await probe(server.url("/redirect"), timeout_seconds=5, policy=_policy(server.port))
    assert result.status_code == 302
    assert result.location == "https://sso.example/login"


async def test_body_is_capped(server: ServerProcess) -> None:
    result = await probe(server.url("/big"), timeout_seconds=5, policy=_policy(server.port))
    assert result.status_code == 200
    assert len(result.body) == MAX_BODY_BYTES


async def test_slow_response_times_out_within_the_deadline(server: ServerProcess) -> None:
    started = time.monotonic()
    result = await probe(
        server.url("/slow?seconds=5"), timeout_seconds=0.5, policy=_policy(server.port)
    )
    elapsed = time.monotonic() - started
    assert result.error == ProbeError.TIMEOUT
    assert elapsed < 2.0  # the timeout plus the one-second hard deadline at most


async def test_trickling_body_cannot_hold_the_probe(server: ServerProcess) -> None:
    started = time.monotonic()
    result = await probe(server.url("/trickle"), timeout_seconds=1, policy=_policy(server.port))
    assert result.status_code == 200  # headers arrived; the verdict does not wait for the body
    assert time.monotonic() - started < 2.5


async def test_connection_refused() -> None:
    port = closed_port()
    # Windows retries a refused localhost connection for about 2 s before reporting it, so the
    # timeout here must be longer than that; Linux refuses immediately.
    result = await probe(f"http://127.0.0.1:{port}/health", timeout_seconds=5, policy=_policy(port))
    assert result.error == ProbeError.CONNECTION_FAILED


async def test_server_that_hangs_up_is_a_protocol_error() -> None:
    with raw_tcp_server(None) as port:
        result = await probe(f"http://127.0.0.1:{port}/", timeout_seconds=2, policy=_policy(port))
    assert result.error == ProbeError.PROTOCOL_ERROR


async def test_garbage_response_is_a_protocol_error() -> None:
    with raw_tcp_server(b"SSH-2.0-OpenSSH_9.6\r\n") as port:
        result = await probe(f"http://127.0.0.1:{port}/", timeout_seconds=2, policy=_policy(port))
    assert result.error == ProbeError.PROTOCOL_ERROR


async def test_tls_to_a_plain_http_server_is_a_tls_error(server: ServerProcess) -> None:
    url = f"https://127.0.0.1:{server.port}/ok"
    result = await probe(url, timeout_seconds=3, policy=_policy(server.port))
    assert result.error == ProbeError.TLS_ERROR


async def test_unlisted_target_is_blocked_before_any_connection(server: ServerProcess) -> None:
    result = await probe(server.url("/ok"), timeout_seconds=2, policy=_policy())
    assert result.error == ProbeError.TARGET_BLOCKED
    assert result.resolved_ip is None


async def test_dns_failure() -> None:
    async def failing_resolver(host: str, port: int) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    result = await probe(
        "https://status.example.com/health",
        timeout_seconds=2,
        policy=TargetPolicy("public", []),
        resolver=failing_resolver,
    )
    assert result.error == ProbeError.DNS_FAILURE


@pytest.mark.parametrize(
    "answer",
    [
        ["10.0.0.7"],  # a public name that now points inside the network (DNS rebinding)
        ["93.184.216.34", "127.0.0.1"],  # one bad address is enough
        ["169.254.169.254"],
    ],
)
async def test_rebinding_to_internal_addresses_is_blocked_at_connection_time(
    answer: list[str],
) -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return answer

    result = await probe(
        "https://status.example.com/health",
        timeout_seconds=2,
        policy=TargetPolicy("public", []),
        resolver=resolver,
    )
    assert result.error == ProbeError.TARGET_BLOCKED
    assert result.error_detail is not None
    assert "resolved to" in result.error_detail


async def test_allowlisted_name_may_resolve_to_loopback_but_never_to_metadata(
    server: ServerProcess,
) -> None:
    async def to_loopback(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    async def to_metadata(host: str, port: int) -> list[str]:
        return ["169.254.169.254"]

    policy = TargetPolicy("allowlist", [f"sandbox:{server.port}"])
    url = f"http://sandbox:{server.port}/ok"
    ok = await probe(url, timeout_seconds=2, policy=policy, resolver=to_loopback)
    assert ok.status_code == 200
    blocked = await probe(url, timeout_seconds=2, policy=policy, resolver=to_metadata)
    assert blocked.error == ProbeError.TARGET_BLOCKED


async def test_slow_resolver_counts_as_a_timeout() -> None:
    async def slow_resolver(host: str, port: int) -> list[str]:
        await anyio.sleep(5)
        return ["93.184.216.34"]

    started = time.monotonic()
    result = await probe(
        "https://status.example.com/health",
        timeout_seconds=0.3,
        policy=TargetPolicy("public", []),
        resolver=slow_resolver,
    )
    assert result.error == ProbeError.TIMEOUT
    assert time.monotonic() - started < 1.5


async def test_an_unused_address_does_not_slow_the_check(server: ServerProcess) -> None:
    """Regression: "localhost" resolves to ::1 first, but the server listens on 127.0.0.1 only.

    Trying addresses strictly in turn made every check wait for ::1 to be refused (about 2 s on
    Windows), so a healthy application was reported as slow and degraded.
    """

    async def dual_stack(host: str, port: int) -> list[str]:
        return ["::1", "127.0.0.1"]

    policy = TargetPolicy("allowlist", [f"dual:{server.port}"])
    result = await probe(
        f"http://dual:{server.port}/ok", timeout_seconds=5, policy=policy, resolver=dual_stack
    )
    assert result.status_code == 200
    assert result.resolved_ip == "127.0.0.1"
    assert result.duration_ms is not None
    assert result.duration_ms < 1000


async def test_every_address_failing_is_a_connection_failure() -> None:
    port = closed_port()

    async def two_dead_addresses(host: str, port: int) -> list[str]:
        return ["127.0.0.1", "127.0.0.2"]

    policy = TargetPolicy("allowlist", [f"dead:{port}"])
    result = await probe(
        f"http://dead:{port}/", timeout_seconds=5, policy=policy, resolver=two_dead_addresses
    )
    assert result.error == ProbeError.CONNECTION_FAILED


def _open_client_sockets(server_port: int) -> int:
    """Sockets in this process still connected to the test server (it runs in another process)."""
    count = 0
    for candidate in gc.get_objects():
        # Network sockets only: on Linux the event loop's own wake-up socket is a Unix socket,
        # whose peer name is a string rather than an address.
        if (
            isinstance(candidate, socket.socket)
            and candidate.fileno() != -1
            and candidate.family in (socket.AF_INET, socket.AF_INET6)
        ):
            with contextlib.suppress(OSError):
                if candidate.getpeername()[1] == server_port:
                    count += 1
    return count


async def test_repeated_abrupt_closes_leak_nothing(server: ServerProcess) -> None:
    """Soak test on one long-running loop, as in production: the paths that hang up mid-exchange
    (body cap, TLS failure, timeout) must leave none of the probe's sockets open."""
    policy = _policy(server.port)
    tls_url = f"https://127.0.0.1:{server.port}/ok"
    for _ in range(25):
        results = await asyncio.gather(
            probe(server.url("/big"), timeout_seconds=5, policy=policy),
            probe(tls_url, timeout_seconds=5, policy=policy),
            probe(server.url("/ok"), timeout_seconds=5, policy=policy),
            probe(server.url("/slow?seconds=0.3"), timeout_seconds=0.1, policy=policy),
        )
        assert results[0].status_code == 200
        assert results[1].error == ProbeError.TLS_ERROR
        assert results[2].status_code == 200
        assert results[3].error == ProbeError.TIMEOUT
    await asyncio.sleep(1.0)  # pending closes complete while the loop keeps running
    gc.collect()
    assert _open_client_sockets(server.port) == 0
