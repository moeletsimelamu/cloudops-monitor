"""Monitoring through the running server: timeouts, failure and recovery, logs, and time zones."""

import time
import urllib.parse
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from tests.servers import ServerProcess
from tests.system.harness import Api, CloudOpsProcess

MANUAL_CHECK_SPACING = 5.5  # "check now" is refused within 5 s of the previous one


class Checker:
    def __init__(self, api: Api, slug: str) -> None:
        self.api, self.slug, self.last = api, slug, 0.0

    def check_now(self) -> tuple[dict[str, Any], float]:
        time.sleep(max(0.0, self.last + MANUAL_CHECK_SPACING - time.monotonic()))
        reply = self.api.call("POST", f"/api/applications/{self.slug}/checks")
        self.last = time.monotonic()
        assert reply.status == 201, reply.body
        result: dict[str, Any] = reply.body
        return result, reply.seconds


def test_a_timeout_opens_an_incident_and_recovery_closes_it(
    cloudops: CloudOpsProcess, target: ServerProcess
) -> None:
    cloudops.start()
    api = cloudops.api
    api.register(
        "orders",
        target.url("/health"),
        interval_seconds=300,  # the test drives the checks itself
        timeout_seconds=2,
        failure_threshold=1,
        recovery_threshold=1,
    )
    checker = Checker(api, "orders")
    check, _ = checker.check_now()
    assert (check["signal"], check["state_after"]) == ("ok", "healthy")

    target.set_health(delay=6)  # answers, but only after the 2 s timeout
    check, seconds = checker.check_now()
    assert (check["signal"], check["reason"]) == ("fail", "timeout")
    assert 1.9 <= check["duration_ms"] / 1000 < 3.5, check
    assert seconds < 4.5, "the timeout is enforced, the server does not wait for the target"
    app = api.get("/api/applications/orders")
    assert app["status"] == "unavailable"
    assert app["open_incident"]["opening_reason"] == "timeout"
    assert app["last_failure_reason"] == "timeout"

    target.set_health(delay=0)
    check, _ = checker.check_now()
    assert (check["signal"], check["state_after"]) == ("ok", "healthy")
    app = api.get("/api/applications/orders")
    assert app["status"] == "healthy"
    assert app["open_incident"] is None
    incidents = api.get("/api/applications/orders/incidents")["items"]
    assert len(incidents) == 1
    assert incidents[0]["closed_at"] is not None

    # The event history tells the same story, in order.
    types = [e["type"] for e in reversed(api.events("&application=orders"))]
    assert types == ["application.registered", "health.unavailable", "health.recovered"]


def test_shipped_logs_are_stored_masked_and_filtered(
    cloudops: CloudOpsProcess, target: ServerProcess
) -> None:
    cloudops.start()
    api = cloudops.api
    api.register("orders", target.url("/health"), interval_seconds=300)
    key = api.call("POST", "/api/applications/orders/ingest-key").body["ingest_key"]
    now = datetime.now(UTC)
    entries = [
        {"severity": "info", "message": "order 1001 placed", "event": "order.placed"},
        {"severity": "warning", "message": "payment retry for order 1002"},
        {
            "severity": "error",
            "message": "database timeout password=hunter2-not-real",
            "event": "order.failed",
            "correlation_id": "req-77",
        },
    ]
    batch = {"entries": [{**e, "timestamp": now.isoformat()} for e in entries]}
    reply = api.call("POST", "/api/ingest/logs", batch, auth=False)
    assert reply.status == 401, "an ingest key is required"
    assert api.call("POST", "/api/ingest/logs", batch).status == 401, (
        "the operator token is not one"
    )
    reply = api.call("POST", "/api/ingest/logs", batch, bearer=key)
    assert (reply.status, reply.body["accepted"]) == (200, 3)

    def messages(query: str) -> list[str]:
        items = api.get(f"/api/logs?application=orders{query}")["items"]
        return [item["message"] for item in items]

    assert len(messages("")) == 3
    assert sorted(messages("&min_severity=warning")) == [
        "database timeout password=[REDACTED]",
        "payment retry for order 1002",
    ]
    assert messages("&q=timeout") == ["database timeout password=[REDACTED]"]
    assert messages("&event=order.placed") == ["order 1001 placed"]
    assert messages("&correlation_id=req-77") == ["database timeout password=[REDACTED]"]
    window = urllib.parse.urlencode(
        {
            "from": (now - timedelta(hours=2)).isoformat(),
            "to": (now - timedelta(hours=1)).isoformat(),
        }
    )
    assert messages("&" + window) == []
    assert "hunter2" not in cloudops.output(), "nor in the server's own log"


def test_timestamps_are_utc_whatever_the_server_time_zone(
    tmp_path: Any, target: ServerProcess
) -> None:
    # UTC+14 on Linux (where TZ is honoured), so any local-time slip shows up as 14 hours.
    server = CloudOpsProcess(
        tmp_path,
        CLOUDOPS_ALLOWED_TARGETS=f"127.0.0.1:{target.port}",
        TZ="Pacific/Kiritimati",
    )
    try:
        server.start()
        api = server.api
        api.register("orders", target.url("/health"), interval_seconds=300)
        check, _ = Checker(api, "orders").check_now()
        started = datetime.fromisoformat(check["started_at"])
        assert check["started_at"].endswith("Z")
        assert abs(started - datetime.now(UTC)) < timedelta(minutes=1)
        for event in api.events():
            assert event["occurred_at"].endswith("Z"), event

        # A range given with any offset means the same instants.
        kiritimati = timezone(timedelta(hours=14))
        around = {
            "from": (started - timedelta(minutes=5)).astimezone(kiritimati).isoformat(),
            "to": (started + timedelta(minutes=5)).astimezone(kiritimati).isoformat(),
        }
        assert (
            len(
                api.get(f"/api/applications/orders/checks?{urllib.parse.urlencode(around)}")[
                    "items"
                ]
            )
            == 1
        )
        before = {
            "from": (started - timedelta(hours=14, minutes=5)).isoformat(),
            "to": (started - timedelta(hours=13, minutes=55)).isoformat(),
        }
        assert (
            api.get(f"/api/applications/orders/checks?{urllib.parse.urlencode(before)}")["items"]
            == []
        )
        # A time without an offset is ambiguous and refused, not guessed.
        naive = {"from": started.replace(tzinfo=None).isoformat()}
        reply = api.call("GET", f"/api/applications/orders/checks?{urllib.parse.urlencode(naive)}")
        assert reply.status == 422
    finally:
        server.close()
