"""Deleting old records in small batches, so each delete holds the write lock only briefly."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from cloudops.config import Settings
from cloudops.db.models import (
    Application,
    HealthCheck,
    LogEntry,
    OperationalEvent,
    OperatorSession,
)
from cloudops.db.session import session_scope

BATCH_SIZE = 5000


@dataclass(frozen=True)
class RetentionResult:
    ran_at: datetime
    health_checks: int
    events: int
    log_entries: int
    sessions: int


def _delete_in_batches(
    factory: sessionmaker[Session],
    id_column: InstrumentedAttribute[int],
    condition: ColumnElement[bool],
) -> int:
    table = id_column.class_
    total = 0
    while True:
        with session_scope(factory) as db:
            ids = select(id_column).where(condition).limit(BATCH_SIZE).scalar_subquery()
            result = db.execute(delete(table).where(id_column.in_(ids)))
            deleted = int(getattr(result, "rowcount", 0))
        total += deleted
        if deleted < BATCH_SIZE:
            return total


def _log_cap_cutoff(factory: sessionmaker[Session], max_rows: int) -> int | None:
    """The highest log entry ID to delete so that only the newest ``max_rows`` remain."""
    with session_scope(factory) as db:
        excess = (db.scalar(select(func.count(LogEntry.id))) or 0) - max_rows
        if excess <= 0:
            return None
        return db.scalar(select(LogEntry.id).order_by(LogEntry.id).offset(excess - 1).limit(1))


def _delete_old_checks(factory: sessionmaker[Session], cutoff: datetime) -> int:
    """Application by application, so each batch is a range search on the (application,
    started_at) index. A condition on ``started_at`` alone cannot use that index, and each batch
    scanned the whole index instead: 18 s for 288,000 of 1.15 million checks. Every check
    belongs to an application (deleting one deletes its checks)."""
    with session_scope(factory) as db:
        application_ids = list(db.scalars(select(Application.id)))
    return sum(
        _delete_in_batches(
            factory,
            HealthCheck.id,
            and_(HealthCheck.application_id == app_id, HealthCheck.started_at < cutoff),
        )
        for app_id in application_ids
    )


def run_retention(
    factory: sessionmaker[Session], settings: Settings, now: datetime
) -> RetentionResult:
    checks = _delete_old_checks(factory, now - timedelta(days=settings.check_retention_days))
    events = _delete_in_batches(
        factory,
        OperationalEvent.id,
        OperationalEvent.occurred_at < now - timedelta(days=settings.event_retention_days),
    )
    logs = _delete_in_batches(
        factory,
        LogEntry.id,
        LogEntry.timestamp < now - timedelta(days=settings.log_retention_days),
    )
    cutoff = _log_cap_cutoff(factory, settings.log_max_rows)
    if cutoff is not None:
        logs += _delete_in_batches(factory, LogEntry.id, LogEntry.id <= cutoff)
    sessions = _delete_in_batches(factory, OperatorSession.id, OperatorSession.expires_at <= now)
    return RetentionResult(now, checks, events, logs, sessions)
