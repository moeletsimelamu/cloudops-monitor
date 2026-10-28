"""The HTTP probe: one GET to a health URL, with pinned connections and hard limits.

httpcore2 is used directly rather than httpx2 because its connection pool accepts a custom
network backend. Ours resolves the host name, checks every resolved address against the target
policy and then connects to an address it checked, so a DNS answer cannot change between the
check and the connection. TLS still verifies the certificate against the original host name. The
probe never follows redirects, never reads proxy settings from the environment, opens a fresh
connection for every check, and reads at most 64 KiB of the body.
"""

import asyncio
import logging
import socket
import ssl
import time
import typing
from collections.abc import Awaitable, Callable
from functools import cache

import anyio
import httpcore2

from cloudops import __version__
from cloudops.domain.classification import Observation, ProbeError
from cloudops.monitoring.target_policy import TargetPolicy, TargetPolicyError

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
CONNECT_STAGGER_SECONDS = 0.25  # RFC 8305 recommends 250 ms between connection attempts
USER_AGENT = f"CloudOpsMonitor/{__version__} (health check)"

Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    """The host's addresses from the operating system resolver, in its preferred order."""
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for *_, sockaddr in infos:
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return addresses


class DNSResolutionError(Exception):
    pass


class TargetBlockedError(Exception):
    pass


class PinnedNetworkBackend(httpcore2.AsyncNetworkBackend):
    """Resolves, checks and connects in one step (no window for DNS rebinding)."""

    def __init__(self, policy: TargetPolicy, *, listed: bool, resolver: Resolver) -> None:
        self.policy = policy
        self.listed = listed
        self.resolver = resolver
        self.inner = httpcore2.AnyIOBackend()
        self.connected_ip: str | None = None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            with anyio.fail_after(timeout):
                addresses = await self.resolver(host, port)
        except TimeoutError:
            raise httpcore2.ConnectTimeout("DNS resolution timed out") from None
        except OSError as error:
            raise DNSResolutionError(str(error)) from error
        if not addresses:
            raise DNSResolutionError("no addresses returned")
        for address in addresses:
            reason = self.policy.blocked_reason(address, listed=self.listed)
            if reason:
                raise TargetBlockedError(f"{host} resolved to {address}, {reason}")

        return await self._connect_first(addresses, port, timeout, local_address, socket_options)

    async def _connect_first(
        self,
        addresses: list[str],
        port: int,
        timeout: float | None,
        local_address: str | None,
        socket_options: typing.Iterable[typing.Any] | None,
    ) -> httpcore2.AsyncNetworkStream:
        """Connect to whichever checked address answers first ("Happy Eyeballs", RFC 8305).

        Attempts start one after another, ``CONNECT_STAGGER_SECONDS`` apart or as soon as the
        previous one fails, and run in parallel from then on. Trying addresses strictly in turn
        would add the full refusal time of an unused address to every check: on Windows a
        refused connection to ``::1`` takes about two seconds, so "localhost" checks of a server
        listening only on 127.0.0.1 looked slow.
        """
        remaining = list(addresses)
        pending: dict[asyncio.Task[httpcore2.AsyncNetworkStream], str] = {}
        errors: list[BaseException] = []

        def start_next() -> None:
            if remaining:
                address = remaining.pop(0)
                task = asyncio.ensure_future(
                    self.inner.connect_tcp(address, port, timeout, local_address, socket_options)
                )
                pending[task] = address

        start_next()
        try:
            while pending:
                done, _ = await asyncio.wait(
                    pending,
                    timeout=CONNECT_STAGGER_SECONDS if remaining else None,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    start_next()  # the current attempts are slow: race the next address too
                    continue
                winner: httpcore2.AsyncNetworkStream | None = None
                for task in done:
                    address = pending.pop(task)
                    error = task.exception()
                    if error is not None:
                        errors.append(error)
                    elif winner is None:
                        winner = task.result()
                        self.connected_ip = address
                    else:
                        await task.result().aclose()  # a second success in the same instant
                if winner is not None:
                    return winner
                start_next()
        finally:
            for task in pending:
                task.cancel()
            if pending:
                # An attempt can complete in the same instant it is cancelled: close it.
                for result in await asyncio.gather(*pending, return_exceptions=True):
                    if isinstance(result, httpcore2.AsyncNetworkStream):
                        await result.aclose()
        if errors:
            raise errors[-1]
        raise httpcore2.ConnectError("no address could be reached")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        raise TargetBlockedError("Unix sockets are never monitored")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


@cache
def _ssl_context() -> ssl.SSLContext:
    return httpcore2.default_ssl_context()


def _caused_by_tls(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def probe(
    url: str,
    *,
    timeout_seconds: float,
    policy: TargetPolicy,
    resolver: Resolver = system_resolver,
    ssl_context: ssl.SSLContext | None = None,
) -> Observation:
    """GET ``url`` and report what happened. Never raises for network or HTTP problems."""
    try:
        target = policy.check(url)
    except TargetPolicyError as error:
        return Observation(None, error=ProbeError.TARGET_BLOCKED, error_detail=str(error))

    backend = PinnedNetworkBackend(policy, listed=policy.is_listed(target), resolver=resolver)
    timeouts = {"connect": timeout_seconds, "read": timeout_seconds, "write": timeout_seconds}
    headers = [(b"User-Agent", USER_AGENT.encode()), (b"Accept", b"application/json, */*")]
    started = time.perf_counter()

    def failed(error: ProbeError, detail: str | None = None) -> Observation:
        return Observation(
            _elapsed_ms(started),
            error=error,
            error_detail=detail,
            resolved_ip=backend.connected_ip,
        )

    try:
        # A hard deadline on top of the per-operation timeouts: a server that trickles bytes
        # cannot hold the probe for longer than this.
        async with (
            asyncio.timeout(timeout_seconds + 1),
            httpcore2.AsyncConnectionPool(
                ssl_context=ssl_context or _ssl_context(),
                network_backend=backend,
                max_connections=1,
                max_keepalive_connections=0,
                retries=0,
            ) as pool,
            pool.stream("GET", url, headers=headers, extensions={"timeout": timeouts}) as response,
        ):
            duration_ms = _elapsed_ms(started)
            body = bytearray()
            # The body only matters for a JSON "status" field; reading it must not change the
            # verdict on a response that already arrived, so it gets what is left of the
            # deadline and no more.
            with anyio.move_on_after(max(0.1, timeout_seconds - duration_ms / 1000)):
                async for chunk in response.aiter_stream():
                    body.extend(chunk)
                    if len(body) >= MAX_BODY_BYTES:
                        break
            location = next(
                (v.decode("latin-1") for k, v in response.headers if k.lower() == b"location"),
                None,
            )
            return Observation(
                duration_ms,
                status_code=response.status,
                body=bytes(body[:MAX_BODY_BYTES]),
                location=location,
                resolved_ip=backend.connected_ip,
            )
    except TargetBlockedError as error:
        return failed(ProbeError.TARGET_BLOCKED, str(error))
    except DNSResolutionError as error:
        return failed(ProbeError.DNS_FAILURE, str(error))
    except (TimeoutError, httpcore2.TimeoutException):
        return failed(ProbeError.TIMEOUT, f"after {timeout_seconds:g} s")
    except httpcore2.ConnectError as error:
        if _caused_by_tls(error):
            return failed(ProbeError.TLS_ERROR, str(error) or None)
        return failed(ProbeError.CONNECTION_FAILED, str(error) or None)
    except (httpcore2.ReadError, httpcore2.WriteError) as error:
        return failed(ProbeError.CONNECTION_FAILED, str(error) or None)
    except (httpcore2.ProtocolError, httpcore2.UnsupportedProtocol) as error:
        return failed(ProbeError.PROTOCOL_ERROR, str(error) or None)
    except Exception as error:
        logger.exception("Unexpected error while probing", extra={"fields": {"url": url}})
        return failed(ProbeError.MONITOR_ERROR, type(error).__name__)
