"""Database failures against the running server: a write lock held by another process, a
damaged database file, and a data directory the server cannot write to."""

import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from tests.servers import ServerProcess
from tests.system.harness import CloudOpsProcess, wait_until

# The worker counts as stalled when it has not completed a tick for this long (worker.py).
WORKER_STALL_SECONDS = 30


def test_a_held_write_lock_gives_503s_then_not_ready_and_everything_recovers(
    cloudops: CloudOpsProcess, target: ServerProcess
) -> None:
    cloudops.start()
    api = cloudops.api
    api.register("orders", target.url("/health"), interval_seconds=15)
    wait_until(lambda: api.checks("orders"), 15, "the first check")

    # Another process (a careless backup, a manual sqlite3 session) holds the write lock.
    holder = sqlite3.connect(cloudops.database, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        locked_at = time.monotonic()

        refused = api.call("POST", "/api/applications/orders/pause")
        assert refused.status == 503
        assert refused.body["error"]["code"] == "database_busy"
        assert "nothing was changed" in refused.body["error"]["message"]
        assert refused.headers["retry-after"] == "5"
        assert 4 < refused.seconds < 15, "it waits for the lock (busy_timeout 5 s) first"
        # Reads still work: SQLite's write-ahead log lets readers carry on.
        assert api.get("/api/applications/orders")["monitoring_enabled"] is True
        assert api.ready_status() == 200

        # Writes keep failing, so the worker cannot finish a tick. After 30 s readiness says so.
        wait_until(
            lambda: api.ready_status() == 503, WORKER_STALL_SECONDS + 15, "readiness to fail"
        )
        assert time.monotonic() - locked_at >= WORKER_STALL_SECONDS - 6
        ready = api.call("GET", "/api/health/ready", auth=False).body
        assert ready["checks"] == {"database": "ok", "schema": "ok", "worker": "error"}
        status = api.get("/api/system/status")["worker"]
        assert status["running"] is True
        assert status["consecutive_tick_failures"] >= 1
        checks_while_locked = len(api.checks("orders"))
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    wait_until(lambda: api.ready_status() == 200, 20, "ready again after the lock is released")
    assert api.call("POST", "/api/applications/orders/pause").status == 200
    assert api.call("POST", "/api/applications/orders/resume").status == 200
    wait_until(lambda: len(api.checks("orders")) > checks_while_locked, 20, "checks to resume")
    assert api.get("/api/system/status")["worker"]["consecutive_tick_failures"] == 0

    records = cloudops.log_records()
    events = [r.get("event") for r in records]
    assert "database.busy" in events
    assert "worker.tick_failed" in events
    assert "readiness.worker_stalled" in events
    busy = next(r for r in records if r.get("event") == "database.busy")
    assert busy["level"] == "warning"
    assert "exception" not in busy, "an expected condition, logged without a traceback"


def test_a_damaged_database_file_stops_start_up_with_a_clear_message(
    cloudops: CloudOpsProcess,
) -> None:
    cloudops.data_dir.mkdir()
    cloudops.database.write_bytes(b"this is not an SQLite database" * 100)
    cloudops.start(wait_ready=False)
    assert cloudops.wait_for_exit() != 0
    records = cloudops.log_records()
    failure = next(r for r in records if r.get("event") == "database.unusable")
    assert failure["level"] == "critical"
    assert str(cloudops.database) in failure["message"]
    assert "file is not a database" in failure["reason"]
    assert cloudops.database.read_bytes().startswith(b"this is not"), "left as it was"


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions and a non-root user",
)
def test_a_data_directory_it_cannot_write_stops_start_up_with_a_clear_message(
    cloudops: CloudOpsProcess,
) -> None:
    cloudops.data_dir.mkdir()
    cloudops.data_dir.chmod(0o555)
    try:
        cloudops.start(wait_ready=False)
        assert cloudops.wait_for_exit() != 0
        failure = next(r for r in cloudops.log_records() if r.get("event") == "database.unusable")
        assert str(cloudops.database) in failure["message"]
        assert "unable to open database file" in failure["reason"]
    finally:
        cloudops.data_dir.chmod(0o755)


def test_a_backup_while_the_server_runs_is_consistent(
    cloudops: CloudOpsProcess, target: ServerProcess, tmp_path: Path
) -> None:
    """``cloudops db backup`` uses SQLite's online backup, so it is safe during writes."""
    cloudops.start()
    api = cloudops.api
    api.register("orders", target.url("/health"), interval_seconds=15)
    wait_until(lambda: api.checks("orders"), 15, "the first check")
    backup = tmp_path / "backup.db"
    from cloudops.cli import backup_database

    backup_database(cloudops.database, backup)
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT slug FROM applications").fetchall() == [("orders",)]
        assert db.execute("SELECT count(*) FROM health_checks").fetchone()[0] >= 1
