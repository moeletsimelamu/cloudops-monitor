"""The monitoring worker: an asyncio task that schedules checks, keeps a heartbeat and runs
retention (docs/ARCHITECTURE.md §3.1).

It runs in the API's event loop. A failing tick is logged and the loop carries on; a failing check
cannot stop the loop or other checks; a slow target only delays its own next check.
"""

import asyncio
import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from cloudops.config import Settings
from cloudops.db.models import Application, SystemState
from cloudops.db.session import run_db_work, session_scope
from cloudops.db.types import utc_now
from cloudops.events import humanize_duration, record_event
from cloudops.monitoring.retention import RetentionResult, run_retention
from cloudops.monitoring.runner import CheckAlreadyRunningError, CheckRunner

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "worker.heartbeat"
MAX_DUE_PER_TICK = 200


@dataclass(frozen=True)
class DueCheck:
    application_id: int
    url: str
    timeout_seconds: int


class MonitoringWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        runner: CheckRunner,
        *,
        clock: Callable[[], datetime] = utc_now,
        tick_seconds: float = 1.0,
        heartbeat_seconds: float = 5.0,
        retention_interval: timedelta = timedelta(hours=1),
        rng: random.Random | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.runner = runner
        self.clock = clock
        self.tick_seconds = tick_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.retention_interval = retention_interval
        self.rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self.started_at: datetime | None = None
        self.last_tick_at: datetime | None = None
        self.loop_lag_ms: float = 0.0
        self.last_retention: RetentionResult | None = None
        self.consecutive_tick_failures = 0
        self._last_heartbeat = float("-inf")
        self._task: asyncio.Task[None] | None = None
        self._checks: set[asyncio.Task[object]] = set()

    # --- lifecycle -------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        self.started_at = self.clock()
        await run_db_work(self._record_start, self.started_at)
        self._task = asyncio.create_task(self._run(), name="cloudops-monitoring-worker")

    async def stop(self) -> None:
        tasks: list[asyncio.Task[object]] = [*self._checks]
        if self._task is not None:
            tasks.append(self._task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None

    def is_healthy(self, now: datetime, max_age: timedelta = timedelta(seconds=30)) -> bool:
        """Running, and ticked recently. A worker that has just started counts from its start
        time, so the moment before the first tick is not reported as a stall, while one that
        never completes a tick is still caught after ``max_age``."""
        reference = self.last_tick_at or self.started_at
        return self.running and reference is not None and now - reference <= max_age

    # --- loop ------------------------------------------------------------------------------

    async def _run(self) -> None:
        next_retention = 0.0
        while True:
            tick_started = time.monotonic()
            try:
                await self.tick()
                if time.monotonic() >= next_retention:
                    next_retention = time.monotonic() + self.retention_interval.total_seconds()
                    await self.retention()
                self.consecutive_tick_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.consecutive_tick_failures += 1
                logger.exception(
                    "Monitoring tick failed",
                    extra={
                        "fields": {
                            "event": "worker.tick_failed",
                            "consecutive_failures": self.consecutive_tick_failures,
                        }
                    },
                )
            elapsed = time.monotonic() - tick_started
            await asyncio.sleep(max(0.0, self.tick_seconds - elapsed))
            # How late the loop woke up: a busy or blocked event loop shows up here.
            overslept = time.monotonic() - tick_started - max(self.tick_seconds, elapsed)
            self.loop_lag_ms = round(max(0.0, overslept) * 1000, 1)

    async def tick(self) -> int:
        """One scheduling pass. Returns the number of checks started."""
        now = self.clock()
        if time.monotonic() - self._last_heartbeat >= self.heartbeat_seconds:
            await run_db_work(self._write_heartbeat, now)
            self._last_heartbeat = time.monotonic()
        due = await run_db_work(self._due_checks, now)
        started = 0
        for item in due:
            try:
                self.runner.claim(item.application_id)
            except CheckAlreadyRunningError:
                continue
            task: asyncio.Task[object] = asyncio.create_task(
                self.runner.run(
                    item.application_id,
                    item.url,
                    item.timeout_seconds,
                    reschedule=self._reschedule,
                )
            )
            self._checks.add(task)
            task.add_done_callback(self._check_finished)
            started += 1
        self.last_tick_at = now
        return started

    async def wait_for_checks(self) -> None:
        """Wait until every check started so far has finished (used by tests)."""
        while self._checks:
            await asyncio.gather(*self._checks, return_exceptions=True)

    async def retention(self) -> None:
        result = await run_db_work(run_retention, self.session_factory, self.settings, self.clock())
        self.last_retention = result
        if result.health_checks or result.events or result.log_entries:
            logger.info(
                "Retention removed old records",
                extra={
                    "fields": {
                        "event": "retention.completed",
                        "health_checks": result.health_checks,
                        "events": result.events,
                        "log_entries": result.log_entries,
                    }
                },
            )

    def _check_finished(self, task: asyncio.Task[object]) -> None:
        self._checks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "Health check task failed",
                exc_info=task.exception(),
                extra={"fields": {"event": "worker.check_failed"}},
            )

    # --- database work (runs in threads) ---------------------------------------------------

    def _due_checks(self, now: datetime) -> list[DueCheck]:
        with session_scope(self.session_factory) as db:
            rows = db.execute(
                select(Application.id, Application.check_url, Application.timeout_seconds)
                .where(
                    Application.monitoring_enabled.is_(True),
                    or_(Application.next_check_at.is_(None), Application.next_check_at <= now),
                )
                .order_by(Application.next_check_at.is_not(None), Application.next_check_at)
                .limit(MAX_DUE_PER_TICK)
            ).all()
        return [DueCheck(r.id, r.check_url, r.timeout_seconds) for r in rows]

    def _reschedule(self, app: Application, started_at: datetime) -> None:
        # Up to 10% jitter spreads checks out so they do not all fire in the same second.
        jitter = self.rng.uniform(0, 0.1) * app.interval_seconds
        app.next_check_at = started_at + timedelta(seconds=app.interval_seconds + jitter)

    def _write_heartbeat(self, now: datetime) -> None:
        with session_scope(self.session_factory) as db:
            state = db.get(SystemState, HEARTBEAT_KEY)
            value = {"at": now.isoformat(), "pid": os.getpid()}
            if state is None:
                db.add(SystemState(key=HEARTBEAT_KEY, value=value, updated_at=now))
            else:
                state.value = value
                state.updated_at = now

    def _record_start(self, now: datetime) -> None:
        """Note the start, and how long checks were not running if the worker was down."""
        with session_scope(self.session_factory) as db:
            state = db.get(SystemState, HEARTBEAT_KEY)
            previous = state.updated_at if state is not None else None
            gap = now - previous if previous is not None else None
            # Anything beyond a few missed heartbeats is a real gap in monitoring.
            if gap is not None and gap > timedelta(seconds=self.heartbeat_seconds * 3 + 5):
                record_event(
                    db,
                    type="monitoring.worker_started",
                    severity="warning",
                    summary=(
                        f"Monitoring resumed after a {humanize_duration(gap)} gap: no checks ran "
                        "during that time"
                    ),
                    occurred_at=now,
                    details={
                        "gap_seconds": int(gap.total_seconds()),
                        "last_heartbeat": previous.isoformat() if previous else None,
                    },
                )
            else:
                record_event(
                    db,
                    type="monitoring.worker_started",
                    severity="info",
                    summary="Monitoring worker started",
                    occurred_at=now,
                    details={"gap_seconds": int(gap.total_seconds()) if gap else None},
                )
        self._write_heartbeat(now)
        self._last_heartbeat = time.monotonic()
