"""Smoke test of a running CloudOps Monitor and sandbox: the primary workflow, over real HTTP.

    python scripts/smoke.py --base http://127.0.0.1:8300 --sandbox http://127.0.0.1:8310 \
        --target http://sandbox:8310/health

The operator token comes from CLOUDOPS_OPERATOR_TOKEN and the sandbox's control token, if it has
one, from SANDBOX_CONTROL_TOKEN. ``--target`` is the sandbox's health URL as CloudOps sees it: in
Docker Compose that is the service name, which must be in CLOUDOPS_ALLOWED_TARGETS.

Standard library only, so it runs anywhere Python does. It creates one application, takes it through
healthy -> degraded -> recovered, ships one log entry, and removes the application again. Exits
non-zero at the first failed step.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any

# The API refuses a second "check now" for the same application within this many seconds.
CHECK_NOW_SPACING = 5.5


class SmokeError(Exception):
    pass


class Client:
    def __init__(self, base: str, headers: dict[str, str] | None = None) -> None:
        self.base = base.rstrip("/")
        self.headers = headers or {}

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, str]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - the URL is the operator's own argument
            self.base + path, data=data, method=method
        )
        for name, value in {**self.headers, **(headers or {})}.items():
            request.add_header(name, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                text = response.read().decode()
                status = response.status
        except urllib.error.HTTPError as error:
            text = error.read().decode()
            status = error.code
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        return status, parsed, text


def step(name: str) -> None:
    print(f"  ok  {name}", flush=True)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def wait_until_ready(api: Client, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    last = "no answer"
    while time.monotonic() < deadline:
        try:
            status, body, _ = api.call("GET", "/api/health/ready")
            if status == 200 and body and body.get("status") == "ready":
                return
            last = f"HTTP {status}: {body}"
        except OSError as error:
            last = str(error)
        time.sleep(1)
    raise SmokeError(f"Not ready after {seconds} s ({last}).")


def check_now(api: Client, slug: str) -> dict[str, Any]:
    time.sleep(CHECK_NOW_SPACING)
    status, body, text = api.call("POST", f"/api/applications/{slug}/checks")
    expect(status == 201, f"Check now returned HTTP {status}: {text[:300]}")
    return dict(body)


def run(args: argparse.Namespace) -> None:
    token = os.environ.get("CLOUDOPS_OPERATOR_TOKEN", "")
    expect(len(token) >= 32, "Set CLOUDOPS_OPERATOR_TOKEN to the server's operator token.")
    anonymous = Client(args.base)
    api = Client(args.base, {"Authorization": f"Bearer {token}"})
    control_token = os.environ.get("SANDBOX_CONTROL_TOKEN")
    sandbox = Client(args.sandbox, {"X-Sandbox-Token": control_token} if control_token else {})
    slug = f"smoke-{uuid.uuid4().hex[:8]}"

    def set_mode(mode: str) -> None:
        status, _, text = sandbox.call("PUT", "/control/mode", {"mode": mode})
        expect(status == 200, f"Sandbox refused mode {mode}: HTTP {status} {text[:200]}")

    wait_until_ready(anonymous)
    step("ready: database, schema and worker")

    if args.expect_frontend:
        for path in ("/", "/applications/anything"):
            status, _, text = anonymous.call("GET", path)
            expect(status == 200 and "<co-root>" in text, f"App shell not served at {path}")
        status, _, _ = anonymous.call("GET", "/main-MISSING0.js")
        expect(status == 404, f"A missing asset returned HTTP {status}, not 404")
        step("app shell served at / and at client routes; missing assets are 404")

    if args.expect_docs_off:
        status, _, _ = anonymous.call("GET", "/api/docs")
        expect(status == 404, f"/api/docs returned HTTP {status}")
        step("API documentation is off")

    status, _, _ = anonymous.call("GET", "/api/health/live", headers={"Host": "attacker.example"})
    expect(status == 400, f"A foreign Host header got HTTP {status}, not 400")
    status, _, _ = anonymous.call("GET", "/api/applications")
    expect(status == 401, f"An anonymous request got HTTP {status}, not 401")
    step("foreign Host header refused; anonymous requests refused")

    status, _, _ = api.call(
        "POST",
        "/api/applications",
        {"slug": slug, "name": "Smoke", "check_url": "http://169.254.169.254/latest/meta-data"},
    )
    expect(status == 422, f"A cloud metadata address was accepted (HTTP {status})")
    step("cloud metadata address refused as a target")

    set_mode("healthy")
    status, _, text = api.call(
        "POST",
        "/api/applications",
        {
            "slug": slug,
            "name": "Smoke test",
            "environment": "smoke",
            "check_url": args.target,
            "interval_seconds": 300,
            "failure_threshold": 1,
            "recovery_threshold": 1,
        },
    )
    expect(status == 201, f"Registering the sandbox failed: HTTP {status} {text[:300]}")
    try:
        check = check_now(api, slug)
        expect(check["signal"] == "ok", f"Healthy sandbox gave signal {check['signal']}")
        _, app, _ = api.call("GET", f"/api/applications/{slug}")
        expect(app["status"] == "healthy", f"Status is {app['status']}, not healthy")
        step(f"registered {args.target}; check now: ok, {check['duration_ms']} ms, healthy")

        set_mode("errors")
        check = check_now(api, slug)
        _, app, _ = api.call("GET", f"/api/applications/{slug}")
        expect(
            app["status"] in ("degraded", "unavailable"),
            f"After a failing check the status is {app['status']}",
        )
        _, incidents, _ = api.call("GET", f"/api/incidents?status=open&application={slug}")
        expect(len(incidents["items"]) == 1, "No open incident after the failing check")
        step(f"sandbox failing: HTTP {check['http_status']}, {app['status']}, incident open")

        set_mode("healthy")
        check_now(api, slug)
        _, app, _ = api.call("GET", f"/api/applications/{slug}")
        expect(app["status"] == "healthy", f"After recovery the status is {app['status']}")
        _, incidents, _ = api.call("GET", f"/api/incidents?status=open&application={slug}")
        expect(not incidents["items"], "The incident is still open after recovery")
        _, events, _ = api.call("GET", f"/api/events?application={slug}")
        types = {event["type"] for event in events["items"]}
        expect(
            {"health.recovered"} <= types and types & {"health.degraded", "health.unavailable"},
            f"Events recorded: {sorted(types)}",
        )
        step("sandbox restored: healthy, incident closed, transitions recorded as events")

        status, key, _ = api.call("POST", f"/api/applications/{slug}/ingest-key")
        expect(status == 201, f"Creating an ingest key returned HTTP {status}")
        shipper = Client(args.base, {"Authorization": f"Bearer {key['ingest_key']}"})
        marker = uuid.uuid4().hex
        status, result, text = shipper.call(
            "POST",
            "/api/ingest/logs",
            {
                "entries": [
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "severity": "error",
                        "event": "smoke.test",
                        "message": f"smoke {marker} password=hunter2-not-real",
                    }
                ]
            },
        )
        expect(status == 200 and result["accepted"] == 1, f"Ingest: HTTP {status} {text[:200]}")
        _, logs, _ = api.call("GET", f"/api/logs?application={slug}&q={marker}")
        expect(len(logs["items"]) == 1, "The shipped log entry was not found")
        expect("hunter2" not in logs["items"][0]["message"], "A password was stored unmasked")
        step("log entry shipped with an ingest key, found by search, password masked")

        _, system, _ = api.call("GET", "/api/system/status")
        expect(system["worker"]["healthy"], "The monitoring worker is not healthy")
        step("monitoring worker healthy")
    finally:
        set_mode("healthy")
        status, _, _ = api.call("DELETE", f"/api/applications/{slug}")
        print(f"  --  removed the smoke-test application (HTTP {status})")
    status, _, _ = api.call("GET", f"/api/applications/{slug}")
    expect(status == 404, "The application still exists after removal")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8300", help="CloudOps Monitor URL")
    parser.add_argument("--sandbox", default="http://127.0.0.1:8310", help="sandbox control URL")
    parser.add_argument(
        "--target",
        default="http://localhost:8310/health",
        help="the sandbox's health URL as CloudOps reaches it",
    )
    parser.add_argument("--expect-frontend", action="store_true", help="check the built app")
    parser.add_argument("--expect-docs-off", action="store_true", help="check /api/docs is off")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        run(args)
    except (SmokeError, OSError, KeyError, TypeError) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1
    print(f"Smoke test passed in {time.monotonic() - started:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
