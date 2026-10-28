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
"""

import asyncio
import hmac
import ipaddress
import os
import random
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

Mode = Literal["healthy", "degraded", "errors", "slow", "unavailable", "flaky"]

_ORDERS = [
    {"id": "ORD-1001", "item": "Laptop stand", "quantity": 2},
    {"id": "ORD-1002", "item": "USB-C dock", "quantity": 1},
]


class ModeChange(BaseModel):
    mode: Mode


class ControlState(BaseModel):
    mode: Mode
    since: datetime
    version: str


def create_app() -> FastAPI:
    version = os.environ.get("SANDBOX_VERSION", "dev")
    slow_seconds = float(os.environ.get("SANDBOX_SLOW_SECONDS", "3"))
    control_token = os.environ.get("SANDBOX_CONTROL_TOKEN") or None
    rng = random.Random()  # noqa: S311 - failure injection, not security
    state = ControlState(mode="healthy", since=datetime.now(UTC), version=version)

    app = FastAPI(title="CloudOps Sandbox", version=version, docs_url=None, redoc_url=None)

    def failing() -> bool:
        return state.mode == "errors" or (state.mode == "flaky" and rng.random() < 0.5)

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
    async def orders() -> JSONResponse:
        if failing() or state.mode == "unavailable":
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
