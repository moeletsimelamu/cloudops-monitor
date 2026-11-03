"""The sandbox service: a small application whose health you can switch on purpose.

It exists so CloudOps Monitor can be demonstrated and tested against a real process over real
HTTP, instead of with simulated data. Every response it gives is a genuine response; only the
choice of which response to give is controlled.

Modes:
- healthy      /health 200 {"status": "pass"}
- degraded     /health 200 {"status": "warn"} (the application reports itself degraded)
- errors       /health and /api/orders return 500 (the "bad deploy" scenario)
- slow         /health answers after SANDBOX_SLOW_SECONDS (default 3)
- unavailable  /health returns 503
- flaky        /health and /api/orders fail about half the time

The mode is changed with PUT /control/mode. When SANDBOX_CONTROL_TOKEN is set, that request
needs the header X-Sandbox-Token; otherwise only requests from this machine are accepted.

With SANDBOX_CLOUDOPS_URL and SANDBOX_INGEST_KEY set, the sandbox ships one structured log entry per
request (except /control) to CloudOps Monitor. With SANDBOX_TRAFFIC_RPS set above zero, it also
calls its own /api/orders at that rate, so there are requests to observe. Those requests are real
but generated, and are logged with the attribute synthetic=true.
"""

import asyncio
import hmac
import ipaddress
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cloudops_sandbox.log_shipper import LogShipper, PostBatch, http_poster

Mode = Literal["healthy", "degraded", "errors", "slow", "unavailable", "flaky"]

logger = logging.getLogger("cloudops_sandbox")

_ORDERS = [
    {"id": "ORD-1001", "item": "Laptop stand", "quantity": 2},
    {"id": "ORD-1002", "item": "USB-C dock", "quantity": 1},
]
_SYNTHETIC_HEADER = "X-Sandbox-Synthetic"


class ModeChange(BaseModel):
    mode: Mode


class ControlState(BaseModel):
    mode: Mode
    since: datetime
    version: str


def _generate_traffic(base_url: str, rps: float, stop: threading.Event) -> None:
    while not stop.wait(1 / rps):
        request = urllib.request.Request(  # noqa: S310 - our own address
            f"{base_url}/api/orders", headers={_SYNTHETIC_HEADER: "1"}
        )
        try:
            with urllib.request.urlopen(request, timeout=5):  # noqa: S310
                pass
        except (urllib.error.URLError, OSError):
            pass  # a failing response is the point; the request log records it


def create_app(post_logs: PostBatch | None = None) -> FastAPI:
    version = os.environ.get("SANDBOX_VERSION", "dev")
    slow_seconds = float(os.environ.get("SANDBOX_SLOW_SECONDS", "3"))
    control_token = os.environ.get("SANDBOX_CONTROL_TOKEN") or None
    traffic_rps = float(os.environ.get("SANDBOX_TRAFFIC_RPS", "0"))
    port = os.environ.get("SANDBOX_PORT", "8310")
    cloudops_url = os.environ.get("SANDBOX_CLOUDOPS_URL")
    ingest_key = os.environ.get("SANDBOX_INGEST_KEY")
    if post_logs is None and cloudops_url and ingest_key:
        post_logs = http_poster(cloudops_url, ingest_key)
    rng = random.Random()  # noqa: S311 - failure injection, not security
    state = ControlState(mode="healthy", since=datetime.now(UTC), version=version)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        shipper = LogShipper(post_logs, flush_seconds=1.0) if post_logs else None
        if shipper is not None:
            logger.addHandler(shipper)
            logger.setLevel(logging.INFO)
        stop_traffic = threading.Event()
        traffic = None
        if traffic_rps > 0:
            traffic = threading.Thread(
                target=_generate_traffic,
                args=(f"http://127.0.0.1:{port}", traffic_rps, stop_traffic),
                daemon=True,
            )
            traffic.start()
        try:
            yield
        finally:
            stop_traffic.set()
            if traffic is not None:
                traffic.join(timeout=6)
            if shipper is not None:
                logger.removeHandler(shipper)
                shipper.close()

    app = FastAPI(
        title="CloudOps Sandbox", version=version, docs_url=None, redoc_url=None, lifespan=lifespan
    )

    def failing() -> bool:
        return state.mode == "errors" or (state.mode == "flaky" and rng.random() < 0.5)

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/control"):
            return await call_next(request)
        correlation_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        response = await call_next(request)
        status = response.status_code
        attributes: dict[str, object] = {
            "method": request.method,
            "path": request.url.path,
            "status_code": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "version": version,
        }
        if _SYNTHETIC_HEADER.lower() in request.headers:
            attributes["synthetic"] = True
        logger.log(
            logging.ERROR if status >= 500 else logging.INFO,
            "%s %s -> %s",
            request.method,
            request.url.path,
            status,
            extra={
                "event": "http.request",
                "correlation_id": correlation_id,
                "attributes": attributes,
            },
        )
        response.headers["X-Request-ID"] = correlation_id
        return response

    @app.get("/health")
    async def health() -> JSONResponse:
        if state.mode == "slow":
            await asyncio.sleep(slow_seconds)
        if state.mode == "unavailable":
            return JSONResponse({"status": "fail"}, status_code=503)
        if failing():
            return JSONResponse({"status": "fail", "error": "order database timeout"}, 500)
        status = "warn" if state.mode == "degraded" else "pass"
        return JSONResponse({"status": status, "version": version})

    @app.get("/api/orders")
    async def orders(request: Request) -> JSONResponse:
        if failing() or state.mode == "unavailable":
            logger.error(
                "Could not load orders: database timeout after 5000 ms",
                extra={
                    "event": "order.load_failed",
                    "correlation_id": request.state.correlation_id,
                    "attributes": {"timeout_ms": 5000, "version": version},
                },
            )
            return JSONResponse({"error": "could not load orders"}, status_code=500)
        return JSONResponse({"items": _ORDERS})

    @app.get("/control", response_model=ControlState)
    async def control() -> ControlState:
        return state

    @app.put("/control/mode", response_model=ControlState)
    async def change_mode(
        change: ModeChange,
        request: Request,
        token: Annotated[str | None, Header(alias="X-Sandbox-Token")] = None,
    ) -> ControlState:
        if control_token is not None:
            if token is None or not hmac.compare_digest(token, control_token):
                raise HTTPException(401, "X-Sandbox-Token is missing or wrong.")
        elif not _is_loopback(request):
            raise HTTPException(403, "Set SANDBOX_CONTROL_TOKEN to control the sandbox remotely.")
        if change.mode != state.mode:
            state.mode = change.mode
            state.since = datetime.now(UTC)
        return state

    return app


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")
