"""Starting, stopping, restarting and killing the real server process."""

import contextlib
import signal
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from tests.servers import ServerProcess
from tests.system.harness import TOKEN, Api, CloudOpsProcess, wait_until

# The worker writes a heartbeat every 5 s and calls a restart a gap in monitoring when the last
# heartbeat is older than three heartbeats plus 5 s (monitoring/worker.py).
GAP_THRESHOLD_SECONDS = 20
# After a clean shutdown uvicorn re-raises the signal it caught, so the process ends "killed by
# SIGTERM" as a container runtime expects (Docker shows 143). On Windows CTRL_BREAK ends it with 3.
STOPPED_BY_SIGNAL = 3 if sys.platform == "win32" else -signal.SIGTERM


def _check_now_ignoring_the_outcome(api: Api) -> None:
    """Start a manual check whose request the test will cut off by killing the server."""
    with contextlib.suppress(OSError):
        api.call("POST", "/api/applications/orders/checks")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"CLOUDOPS_OPERATOR_TOKEN": ""}, "CLOUDOPS_OPERATOR_TOKEN is required"),
        (
            {"CLOUDOPS_OPERATOR_TOKEN": "short-but-secret-value"},
            "CLOUDOPS_OPERATOR_TOKEN must be at least 32 characters long.",
        ),
        ({"CLOUDOPS_SESSION_HOURS": "twelve"}, "CLOUDOPS_SESSION_HOURS: Input should be"),
        ({"CLOUDOPS_SESSION_HOURS": "1000"}, "CLOUDOPS_SESSION_HOURS: Input should be less"),
        ({"CLOUDOPS_AUTH_MODE": "tokens"}, "CLOUDOPS_AUTH_MODE: Input should be 'token'"),
        ({"CLOUDOPS_ALLOWED_TARGETS": "http://x.example"}, "must look like host:port"),
        ({"CLOUDOPS_GITHUB_API_URL": "http://api.github.com"}, "must use https"),
    ],
)
def test_invalid_configuration_stops_start_up_with_one_readable_line(
    tmp_path: Path, env: dict[str, str], expected: str
) -> None:
    server = CloudOpsProcess(tmp_path, **env)
    try:
        server.start(wait_ready=False)
        assert server.wait_for_exit() == 2
        records = server.log_records()  # every line is JSON
        assert len(records) == 1, server.output()
        record = records[0]
        assert record["level"] == "critical"
        assert record["event"] == "config.invalid"
        assert any(expected in error for error in record["errors"]), record["errors"]
        assert "Traceback" not in server.output()
        for secret_value in ("short-but-secret-value", TOKEN):
            assert secret_value not in server.output()
        assert not server.database.exists(), "nothing is created before the settings are valid"
    finally:
        server.close()


def test_graceful_stop_and_restart_keep_history_and_monitoring_continues(
    cloudops: CloudOpsProcess, target: ServerProcess
) -> None:
    cloudops.start()
    api = cloudops.api
    api.register("orders", target.url("/health"), interval_seconds=15)
    wait_until(lambda: api.checks("orders"), 15, "the worker ran the first check")

    assert cloudops.stop() == STOPPED_BY_SIGNAL
    first_run = cloudops.log_records()  # all JSON, uvicorn's own lines included
    assert [r.get("event") for r in first_run].count("app.stopped") == 1
    assert any(r["logger"].startswith("uvicorn") for r in first_run)
    assert not any("exception" in r for r in first_run), "a clean stop logs no errors"

    cloudops.start()
    assert [c["signal"] for c in api.checks("orders")] == ["ok"]
    assert api.get("/api/applications/orders")["status"] == "healthy"
    starts = api.events("&type=monitoring.worker_started")
    assert len(starts) == 2
    assert starts[0]["severity"] == "info", "a quick restart is not a gap in monitoring"
    # The worker is running again: "check now" goes through the same runner and is recorded.
    assert api.call("POST", "/api/applications/orders/checks").status == 201
    assert len(api.checks("orders")) == 2


def test_a_killed_server_restarts_with_a_consistent_database_and_reports_the_gap(
    cloudops: CloudOpsProcess, target: ServerProcess
) -> None:
    cloudops.start()
    api = cloudops.api
    api.register("orders", target.url("/health"), interval_seconds=15, timeout_seconds=12)
    wait_until(lambda: api.checks("orders"), 15, "the first check")
    recorded_before = len(api.checks("orders"))

    # A check is in flight (the target takes 10 s to answer) when the process is killed.
    target.set_health(delay=10)
    threading.Thread(target=_check_now_ignoring_the_outcome, args=(api,), daemon=True).start()
    wait_until(
        lambda: api.get("/api/system/status")["worker"]["checks_in_flight"] >= 1,
        10,
        "a check is in flight",
    )
    killed_at = time.monotonic()
    cloudops.kill()

    # The database is whole: SQLite's journal makes an interrupted write all or nothing.
    with closing(sqlite3.connect(cloudops.database)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    target.set_health(delay=0)
    remaining = GAP_THRESHOLD_SECONDS + 2 - (time.monotonic() - killed_at)
    if remaining > 0:
        time.sleep(remaining)  # a real outage longer than the gap threshold
    cloudops.start()

    start = api.events("&type=monitoring.worker_started")[0]
    assert start["severity"] == "warning"
    assert "Monitoring resumed after a" in start["summary"]
    assert start["details"]["gap_seconds"] >= GAP_THRESHOLD_SECONDS
    # The interrupted check was never recorded, and was not left half-recorded. The application
    # was overdue, so the restarted worker checks it straight away.
    wait_until(
        lambda: len(api.checks("orders")) > recorded_before, 10, "a check soon after restart"
    )
    checks = api.checks("orders")
    assert len(checks) == recorded_before + 1
    assert checks[0]["signal"] == "ok"
    assert api.get("/api/system/status")["worker"]["healthy"] is True
