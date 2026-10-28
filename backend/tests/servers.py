"""Real local HTTP and TCP servers for probe tests: the probe is exercised over actual sockets."""

import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route


async def _status(request: Request) -> Response:
    code = int(request.path_params["code"])
    body = request.query_params.get("body")
    return Response(body or "", status_code=code, media_type="application/json")


async def _ok(_request: Request) -> Response:
    return JSONResponse({"status": "pass"})


async def _slow(request: Request) -> Response:
    await asyncio.sleep(float(request.query_params.get("seconds", "2")))
    return JSONResponse({"status": "pass"})


async def _redirect(_request: Request) -> Response:
    return Response(status_code=302, headers={"Location": "https://sso.example/login"})


async def _big(_request: Request) -> Response:
    payload = json.dumps({"status": "warn", "padding": "x" * (1024 * 1024)})
    return Response(payload, media_type="application/json")


async def _trickle(_request: Request) -> Response:
    async def drip() -> Any:
        for _ in range(100):
            yield b" "
            await asyncio.sleep(0.2)

    return StreamingResponse(drip(), media_type="application/json")


class ControllableApp:
    """A Starlette app with a fixed set of behaviours plus a switchable ``/health``."""

    def __init__(self) -> None:
        self.health_status = 200
        self.health_body: dict[str, str] = {"status": "pass"}
        self.health_delay = 0.0
        self.requests = 0
        self.app = Starlette(
            routes=[
                Route("/ok", _ok),
                Route("/status/{code:int}", _status),
                Route("/slow", _slow),
                Route("/redirect", _redirect),
                Route("/big", _big),
                Route("/trickle", _trickle),
                Route("/health", self._health),
            ]
        )

    async def _health(self, _request: Request) -> Response:
        self.requests += 1
        if self.health_delay:
            await asyncio.sleep(self.health_delay)
        return JSONResponse(self.health_body, status_code=self.health_status)


class ServerThread:
    def __init__(self, app: Any) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "ServerThread":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.01)
        return self

    @property
    def port(self) -> int:
        return int(self.server.servers[0].sockets[0].getsockname()[1])

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __exit__(self, *_exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@contextlib.contextmanager
def raw_tcp_server(reply: bytes | None) -> Iterator[int]:
    """Accepts connections and sends ``reply`` (or nothing) before closing them."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            with connection, contextlib.suppress(OSError):
                connection.recv(4096)
                if reply:
                    connection.sendall(reply)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield int(listener.getsockname()[1])
    finally:
        stop.set()
        thread.join(timeout=5)
        listener.close()


def closed_port() -> int:
    """A port with nothing listening on it (connection refused)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return int(probe_socket.getsockname()[1])
