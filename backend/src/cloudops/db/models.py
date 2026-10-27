"""SQLAlchemy models. The schema itself is created and changed only by Alembic migrations.

See docs/ARCHITECTURE.md §8 for the reasoning behind tables, keys and indexes.
"""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from cloudops.db.types import UTCDateTime, utc_now

HEALTH_STATES = ("unknown", "healthy", "degraded", "unavailable")
SIGNALS = ("ok", "warn", "fail", "inconclusive")
EVENT_SEVERITIES = ("info", "warning", "error", "critical")
DEPLOYMENT_OUTCOMES = (
    "queued",
    "in_progress",
    "succeeded",
    "failed",
    "cancelled",
    "needs_attention",
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# Deterministic constraint names, so migrations can refer to them (SQLite batch mode needs this).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        dict[str, Any]: JSON(),
    }


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(_one_of("health_state", HEALTH_STATES), name="health_state"),
        CheckConstraint("interval_seconds BETWEEN 15 AND 3600", name="interval_seconds"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 30", name="timeout_seconds"),
        CheckConstraint("failure_threshold BETWEEN 1 AND 5", name="failure_threshold"),
        CheckConstraint("recovery_threshold BETWEEN 1 AND 5", name="recovery_threshold"),
        Index("ix_applications_next_check_at", "next_check_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    environment: Mapped[str] = mapped_column(String(20))

    # Monitoring configuration (one per application, so it lives here; see ARCHITECTURE §8).
    check_url: Mapped[str] = mapped_column(String(2048))
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=5)
    slow_threshold_ms: Mapped[int] = mapped_column(Integer, default=1000)
    failure_threshold: Mapped[int] = mapped_column(Integer, default=2)
    recovery_threshold: Mapped[int] = mapped_column(Integer, default=2)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Current health, maintained by the monitoring worker.
    health_state: Mapped[str] = mapped_column(String(16), default="unknown")
    state_since: Mapped[datetime | None]
    last_check_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_failure_at: Mapped[datetime | None]
    last_failure_reason: Mapped[str | None] = mapped_column(String(32))
    next_check_at: Mapped[datetime | None]

    # Deployment source (GitHub Actions).
    github_repo: Mapped[str | None] = mapped_column(String(140))
    deploy_workflow: Mapped[str | None] = mapped_column(String(100))
    deploy_branch: Mapped[str | None] = mapped_column(String(255))

    # SHA-256 of the log ingest key; the key itself is shown once and never stored.
    ingest_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True)

    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class HealthCheck(Base):
    __tablename__ = "health_checks"
    __table_args__ = (
        CheckConstraint(_one_of("signal", SIGNALS), name="signal"),
        CheckConstraint(_one_of("state_after", HEALTH_STATES), name="state_after"),
        Index("ix_health_checks_application_started", "application_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    started_at: Mapped[datetime]
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    signal: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(String(300))
    resolved_ip: Mapped[str | None] = mapped_column(String(45))
    state_after: Mapped[str] = mapped_column(String(16))


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(_one_of("worst_state", ("degraded", "unavailable")), name="worst_state"),
        Index("ix_incidents_application_opened", "application_id", "opened_at"),
        # At most one open incident per application: a persistent failure cannot open another.
        Index(
            "uq_incidents_one_open_per_application",
            "application_id",
            unique=True,
            sqlite_where=text("closed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    worst_state: Mapped[str] = mapped_column(String(16))
    opening_reason: Mapped[str | None] = mapped_column(String(32))


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(_one_of("outcome", DEPLOYMENT_OUTCOMES), name="outcome"),
        # Idempotent sync. Includes the application, because two applications may legitimately
        # be deployed by the same workflow run.
        UniqueConstraint("application_id", "source", "external_id", "attempt"),
        Index("ix_deployments_application_queued", "application_id", "queued_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    run_number: Mapped[int | None] = mapped_column(Integer)
    workflow_name: Mapped[str | None] = mapped_column(String(100))
    commit_sha: Mapped[str] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(255))
    trigger: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(300))
    outcome: Mapped[str] = mapped_column(String(20))
    queued_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    url: Mapped[str | None] = mapped_column(String(500))
    synced_at: Mapped[datetime] = mapped_column(default=utc_now)


class OperationalEvent(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(_one_of("severity", EVENT_SEVERITIES), name="severity"),
        Index("ix_events_occurred_at", "occurred_at"),
        Index("ix_events_application_occurred", "application_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime]
    type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(10))
    # Events outlive the records they mention (the audit trail survives a removed application);
    # the summary and details keep the names needed to read them.
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL")
    )
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_application_timestamp", "application_id", "timestamp"),
        Index("ix_log_entries_timestamp", "timestamp"),
        Index("ix_log_entries_correlation_id", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    timestamp: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(default=utc_now)
    # Python logging levels (10 debug … 50 critical), so "at least warning" is a range query.
    severity: Mapped[int] = mapped_column(SmallInteger)
    event: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(4096))
    correlation_id: Mapped[str | None] = mapped_column(String(100))
    environment: Mapped[str | None] = mapped_column(String(20))
    attributes: Mapped[dict[str, Any] | None]


class OperatorSession(Base):
    __tablename__ = "operator_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SHA-256 of the cookie value; a copy of the database does not contain usable sessions.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    expires_at: Mapped[datetime]


class SystemState(Base):
    """Small key/value records: worker heartbeat, sync cursors and ETags."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)
