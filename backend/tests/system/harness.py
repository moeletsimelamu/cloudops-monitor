"""Real CloudOps Monitor processes for system tests.

The server is started with ``cloudops serve``, the container's command, as a child process with
its own data directory, and driven over HTTP. Stopping it is done the way an operator or a
container runtime would: a termination signal (``CTRL_BREAK`` on Windows), or killing it outright.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

TOKEN = "system-test-operator-token-0123456789-abcdef"
START_TIMEOUT = 30.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until[T](
    condition: Callable[[], T], timeout: float, message: str, interval: float = 0.25
) -> T:
    """Poll ``condition`` until it returns something truthy; fail with ``message`` otherwise."""
    deadline = time.monotonic() + timeout
    while True:
        result = condition()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out after {timeout:.0f} s: {message}")
        time.sleep(interval)


def _lower(headers: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


@dataclass
class Reply:
    """``headers`` has lower-case names."""

    status: int
    body: Any
    headers: dict[str, str]
    seconds: float


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        auth: bool = True,
        bearer: str | None = None,
        timeout: float = 30.0,
    ) -> Reply:
        """``auth`` sends the operator token; ``bearer`` another credential (an ingest key)."""
        headers = {"Content-Type": "application/json"}
        if auth or bearer:
            headers["Authorization"] = f"Bearer {bearer or TOKEN}"
        request = urllib.request.Request(  # noqa: S310 - a local test server
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers=headers,
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read()
                status, response_headers = response.status, _lower(response.headers)
        except urllib.error.HTTPError as error:
            with error:  # an HTTPError holds the response open until closed
                raw = error.read()
                status, response_headers = error.code, _lower(error.headers)
        return Reply(
            status,
            json.loads(raw) if raw else None,
            response_headers,
            time.monotonic() - started,
        )

    def get(self, path: str) -> Any:
        reply = self.call("GET", path)
        assert reply.status == 200, (path, reply.status, reply.body)
        return reply.body

    def ready_status(self) -> int | None:
        try:
            return self.call("GET", "/api/health/ready", auth=False, timeout=5).status
        except OSError:
            return None  # not listening (yet, or any more)

    def register(self, slug: str, check_url: str, **fields: object) -> dict[str, Any]:
        body = {"slug": slug, "name": slug, "environment": "test", "check_url": check_url}
        reply = self.call("POST", "/api/applications", {**body, **fields})
        assert reply.status == 201, reply.body
        result: dict[str, Any] = reply.body
        return result

    def checks(self, slug: str, limit: int = 100) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.get(f"/api/applications/{slug}/checks?limit={limit}")[
            "items"
        ]
        return items

    def events(self, query: str = "") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.get(f"/api/events?limit=200{query}")["items"]
        return items


class CloudOpsProcess:
    """One data directory, started and stopped as often as a test needs."""

    def __init__(self, workdir: Path, **env: str) -> None:
        self.workdir = workdir
        self.data_dir = workdir / "data"
        self.extra_env = env
        self.port = free_port()
        self.api = Api(f"http://127.0.0.1:{self.port}")
        self.process: subprocess.Popen[bytes] | None = None
        self.runs = 0
        self._log: IO[bytes] | None = None

    @property
    def database(self) -> Path:
        return self.data_dir / "cloudops.db"

    @property
    def log_path(self) -> Path:
        return self.workdir / f"server-{self.runs}.log"

    def environment(self) -> dict[str, str]:
        # Nothing from the developer's own CLOUDOPS_* settings, and no .env file (cwd is empty).
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLOUDOPS_")}
        env.update(
            CLOUDOPS_DATA_DIR=str(self.data_dir),
            CLOUDOPS_OPERATOR_TOKEN=TOKEN,
            CLOUDOPS_LOG_FORMAT="json",
            # Deployment sync is not under test here and must not call GitHub.
            CLOUDOPS_GITHUB_API_URL="https://127.0.0.1:9",
        )
        env.update(self.extra_env)
        return env

    def start(self, *, wait_ready: bool = True) -> None:
        assert self.process is None or self.process.poll() is not None, "already running"
        self.runs += 1
        self._log = self.log_path.open("wb")
        flags = 0
        if sys.platform == "win32":  # a statement, so mypy checks it per platform
            flags = subprocess.CREATE_NEW_PROCESS_GROUP  # CTRL_BREAK reaches only this process
        self.process = subprocess.Popen(  # noqa: S603 - fixed arguments
            [sys.executable, "-m", "cloudops.cli", "serve", "--port", str(self.port)],
            cwd=self.workdir,
            env=self.environment(),
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        if wait_ready:
            wait_until(
                lambda: self.api.ready_status() == 200 or self.process_exited(),
                START_TIMEOUT,
                "the server did not become ready",
            )
            assert not self.process_exited(), self.output()

    def process_exited(self) -> bool:
        return self.process is not None and self.process.poll() is not None

    def wait_for_exit(self, timeout: float = START_TIMEOUT) -> int:
        assert self.process is not None
        code = self.process.wait(timeout=timeout)
        self._close_log()
        return code

    def stop(self, timeout: float = 30.0) -> int:
        """Ask the server to stop, as ``docker stop`` or Ctrl+C would, and wait for it."""
        assert self.process is not None
        if sys.platform == "win32":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.process.send_signal(signal.SIGTERM)
        return self.wait_for_exit(timeout)

    def kill(self) -> None:
        """Stop it with no chance to clean up: SIGKILL, or TerminateProcess on Windows."""
        assert self.process is not None
        self.process.kill()
        self.wait_for_exit()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None

    def output(self, run: int | None = None) -> str:
        path = self.workdir / f"server-{run or self.runs}.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def log_records(self, run: int | None = None) -> list[dict[str, Any]]:
        """Every line of output as JSON. A line that is not JSON fails the test."""
        records = []
        for line in self.output(run).splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    raise AssertionError(f"Output line is not JSON: {line[:300]}") from None
        return records

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=30)
        self._close_log()
