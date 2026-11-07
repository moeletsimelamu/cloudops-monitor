"""Real local servers for probe tests: the probe is exercised over actual sockets.

The HTTP server runs in its own process (``python -m tests.servers``). In the same process its
connections would be garbage collected at unpredictable moments and look like leaks in whatever
test was running. Kept apart, any unclosed socket in the test process is the probe's own.
"""

import asyncio
import contextlib
import json
import socket
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

BACKEND_DIR = Path(__file__).resolve().parents[1]


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
        for _ in range(25):
            yield b" "
            await asyncio.sleep(0.2)

    return StreamingResponse(drip(), media_type="application/json")


def build_app() -> Starlette:
    """A fixed set of behaviours, plus a ``/health`` switched through ``/_control/health``."""
    health: dict[str, Any] = {"status": 200, "body": {"status": "pass"}}

    async def show_health(_request: Request) -> Response:
        return JSONResponse(health["body"], status_code=health["status"])

    async def set_health(request: Request) -> Response:
        health.update(await request.json())
        return JSONResponse(health)

    return Starlette(
        routes=[
            Route("/ok", _ok),
            Route("/status/{code:int}", _status),
            Route("/slow", _slow),
            Route("/redirect", _redirect),
            Route("/big", _big),
            Route("/trickle", _trickle),
            Route("/health", show_health),
            Route("/_control/health", set_health, methods=["POST"]),
        ]
    )


def _serve() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    # Listen before announcing the port: connections then wait in the backlog until uvicorn
    # accepts them. Announcing first was a race that Windows hid (it retries a refused connection
    # for about 2 s) and Linux did not.
    listener.listen(128)
    print(listener.getsockname()[1], flush=True)  # the parent reads the port from this line
    config = uvicorn.Config(build_app(), log_level="critical", lifespan="off")
    uvicorn.Server(config).run(sockets=[listener])


class ServerProcess:
    """The HTTP test server as a child process, stopped when the block ends."""

    def __enter__(self) -> "ServerProcess":
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.servers"],
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert self.process.stdout is not None
        line = self.process.stdout.readline().strip()
        if not line.isdigit():
            self.__exit__()
            raise RuntimeError("test server did not start")
        self.port = int(line)
        return self

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def set_health(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        request = urllib.request.Request(  # noqa: S310 - our own test server
            self.url("/_control/health"),
            data=json.dumps({"status": status, "body": body or {"status": "pass"}}).encode(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass

    def __exit__(self, *_exc: object) -> None:
        self.process.terminate()
        self.process.wait(timeout=15)
        if self.process.stdout is not None:
            self.process.stdout.close()


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


if __name__ == "__main__":
    _serve()
