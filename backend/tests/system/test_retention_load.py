"""Retention on a database holding 40 days of checks, while the API is in use.

The default scale is 10 applications checked every 30 seconds for 40 days: 1,152,000 checks, of
which the 10 days beyond the 30-day retention (288,000) are deleted at start-up. Set
``CLOUDOPS_LOAD_APPS`` for another number of applications. The measurements are printed
(``pytest -rP`` shows them).
"""

import os
import sqlite3
import statistics
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.servers import ServerProcess
from tests.system.harness import CloudOpsProcess, Reply, wait_until

APPS = int(os.environ.get("CLOUDOPS_LOAD_APPS", "10"))
INTERVAL = timedelta(seconds=30)
SEEDED_DAYS = 40
RETENTION_DAYS = 30  # the default CLOUDOPS_CHECK_RETENTION_DAYS


def _stored(moment: datetime) -> str:
    """How SQLAlchemy stores a UTC timestamp in SQLite (naive UTC, microseconds)."""
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _seed(database: str, app_ids: list[int], newest: datetime) -> list[datetime]:
    """Checks for every application, interleaved in time as the worker would write them."""
    times = []
    moment = newest - timedelta(days=SEEDED_DAYS)
    while moment <= newest:
        times.append(moment)
        moment += INTERVAL
    rows = (
        (app_id, _stored(at), 42, 200, "ok", None, None, "127.0.0.1", "healthy")
        for at in times
        for app_id in app_ids
    )
    with closing(sqlite3.connect(database)) as db:
        db.executemany(
            "INSERT INTO health_checks (application_id, started_at, duration_ms, http_status, "
            "signal, reason, detail, resolved_ip, state_after) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        db.commit()
    return times


def test_retention_deletes_exactly_the_old_checks_while_the_api_stays_responsive(
    cloudops: CloudOpsProcess, target: ServerProcess
) -> None:
    cloudops.start()
    slugs = [f"app-{n:02d}" for n in range(APPS)]
    app_ids = [
        cloudops.api.register(slug, target.url("/health"), interval_seconds=15)["id"]
        for slug in slugs
    ]
    cloudops.stop()

    seed_started = time.monotonic()
    times = _seed(str(cloudops.database), app_ids, datetime.now(UTC) - timedelta(minutes=5))
    seed_seconds = time.monotonic() - seed_started
    seeded = len(times) * APPS

    # Readers hit two busy pages from before the server is up until retention has finished, and
    # then for as long again, for comparison.
    replies: list[Reply] = []
    stop_reading = threading.Event()

    def read() -> None:
        paths = ["/api/overview", f"/api/applications/{slugs[0]}/checks?limit=200"]
        n = 0
        while not stop_reading.is_set():
            try:
                replies.append(cloudops.api.call("GET", paths[n % 2], timeout=30))
            except OSError:
                time.sleep(0.05)  # not listening yet
            n += 1

    readers = [threading.Thread(target=read, daemon=True) for _ in range(2)]
    for reader in readers:
        reader.start()
    restart = time.monotonic()
    cloudops.start(wait_ready=False)

    def retention_finished() -> dict[str, Any] | None:
        if cloudops.api.ready_status() is None:
            return None
        status: dict[str, Any] = cloudops.api.get("/api/system/status")
        return status if status["worker"]["last_retention_at"] else None

    status = wait_until(
        retention_finished,
        180,
        "retention to finish",
        interval=0.5,
    )
    assert status is not None
    during = list(replies)
    window = time.monotonic() - restart
    time.sleep(max(5.0, window))
    stop_reading.set()
    for reader in readers:
        reader.join(timeout=60)
    after = replies[len(during) :]

    ran_at = datetime.fromisoformat(status["worker"]["last_retention_at"])
    cutoff = ran_at - timedelta(days=RETENTION_DAYS)
    expected = sum(1 for at in times if at < cutoff) * APPS
    deleted = status["worker"]["last_retention_deleted"]["health_checks"]
    assert deleted == expected
    with closing(sqlite3.connect(cloudops.database)) as db:
        assert db.execute(
            "SELECT count(*) FROM health_checks WHERE started_at < ?", (_stored(cutoff),)
        ).fetchone() == (0,)
        remaining_seeded = db.execute(
            "SELECT count(*) FROM health_checks WHERE started_at <= ?", (_stored(times[-1]),)
        ).fetchone()[0]
    assert remaining_seeded == seeded - expected
    completed = next(r for r in cloudops.log_records() if r.get("event") == "retention.completed")
    assert completed["health_checks"] == expected

    assert during and after, "the readers ran"
    assert {reply.status for reply in replies} == {200}
    assert max(reply.seconds for reply in during) < 3.0, "no read waited long behind retention"
    # Monitoring carried on: every application has been checked since the restart.
    for slug in slugs:
        latest = cloudops.api.checks(slug, limit=1)[0]["started_at"]
        assert datetime.fromisoformat(latest) > ran_at - timedelta(seconds=window + 1), slug
    summary = (
        f"\nseeded {seeded:,} checks for {APPS} applications in {seed_seconds:.1f} s; "
        f"retention deleted {deleted:,} in {completed['duration_ms'] / 1000:.1f} s; "
        f"database {status['database']['size_bytes']:,} bytes"
        f"\nreads during retention ({window:.1f} s from start-up): {_summary(during)}"
        f"\nreads afterwards: {_summary(after)}"
    )
    print(summary)
    if measurements := os.environ.get("CLOUDOPS_MEASUREMENTS"):  # set in CI
        with Path(measurements).open("a", encoding="utf-8") as file:
            file.write(" | ".join(summary.strip().splitlines()) + "\n")


def _summary(replies: list[Reply]) -> str:
    latencies = sorted(reply.seconds * 1000 for reply in replies)
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
    return (
        f"{len(latencies)} requests, median {statistics.median(latencies):.0f} ms, "
        f"p95 {p95:.0f} ms, max {latencies[-1]:.0f} ms"
    )
