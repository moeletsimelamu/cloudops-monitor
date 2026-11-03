"""Running one check end to end: probe, then record. Shared by the scheduler and "check now".

Probes run on the event loop; database work runs in a worker thread, because the monitoring
worker shares the event loop with the API and must never block it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from cloudops.config import Settings
from cloudops.db.models import Application
from cloudops.db.session import run_db_work, session_scope
from cloudops.db.types import utc_now
from cloudops.domain.classification import Observation, ProbeError
from cloudops.monitoring.probe import probe
from cloudops.monitoring.recorder import RecordedCheck, record_check
from cloudops.monitoring.target_policy import TargetPolicy

logger = logging.getLogger(__name__)


class ProbeFunction(Protocol):
    def __call__(
        self, url: str, *, timeout_seconds: float, policy: TargetPolicy
    ) -> Awaitable[Observation]: ...


class CheckAlreadyRunningError(Exception):
    pass


class CheckRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        probe_fn: ProbeFunction = probe,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_factory = session_factory
        self.policy = TargetPolicy.from_settings(settings)
        self.probe_fn = probe_fn
        self.clock = clock
        self.max_concurrency = settings.monitor_max_concurrency
        self.in_flight: set[int] = set()
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def semaphore(self) -> asyncio.Semaphore:
        # Created on first use, inside the running event loop.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        return self._semaphore

    def claim(self, application_id: int) -> None:
        """Mark a check as running; at most one check per application at a time."""
        if application_id in self.in_flight:
            raise CheckAlreadyRunningError
        self.in_flight.add(application_id)

    async def run(
        self,
        application_id: int,
        url: str,
        timeout_seconds: int,
        *,
        manual: bool = False,
        reschedule: Callable[[Application, datetime], None] | None = None,
    ) -> RecordedCheck | None:
        """Probe and record. The caller must have claimed ``application_id``."""
        try:
            async with self.semaphore:
                started_at = self.clock()
                try:
                    observation = await self.probe_fn(
                        url, timeout_seconds=timeout_seconds, policy=self.policy
                    )
                except Exception as error:  # the probe itself should never raise
                    logger.exception("Probe raised unexpectedly")
                    observation = Observation(
                        None, error=ProbeError.MONITOR_ERROR, error_detail=type(error).__name__
                    )
            return await run_db_work(
                self._record, application_id, observation, started_at, manual, reschedule
            )
        finally:
            self.in_flight.discard(application_id)

    def _record(
        self,
        application_id: int,
        observation: Observation,
        started_at: datetime,
        manual: bool,
        reschedule: Callable[[Application, datetime], None] | None,
    ) -> RecordedCheck | None:
        with session_scope(self.session_factory) as db:
            result = record_check(db, application_id, observation, started_at, manual=manual)
            app = db.get(Application, application_id)
            if app is not None and reschedule is not None:
                reschedule(app, started_at)
        if result is not None:
            logger.info(
                "Health check %s: %s",
                result.outcome.signal,
                result.outcome.reason or "ok",
                extra={
                    "fields": {
                        "event": "health_check.recorded",
                        "application_id": application_id,
                        "check_id": result.check_id,
                        "signal": result.outcome.signal,
                        "reason": result.outcome.reason,
                        "http_status": observation.status_code,
                        "duration_ms": observation.duration_ms,
                        "state": result.state_after,
                        "manual": manual,
                    }
                },
            )
        return result
