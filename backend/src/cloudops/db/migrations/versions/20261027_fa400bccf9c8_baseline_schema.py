"""Baseline schema: every table of the first release.

Revision ID: fa400bccf9c8
Revises:
Create Date: 2026-10-27 10:33:02.765133
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import cloudops.db.types

revision: str = "fa400bccf9c8"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("check_url", sa.String(length=2048), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("slow_threshold_ms", sa.Integer(), nullable=False),
        sa.Column("failure_threshold", sa.Integer(), nullable=False),
        sa.Column("recovery_threshold", sa.Integer(), nullable=False),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False),
        sa.Column("health_state", sa.String(length=16), nullable=False),
        sa.Column("state_since", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("last_check_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("last_success_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("last_failure_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("last_failure_reason", sa.String(length=32), nullable=True),
        sa.Column("next_check_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("github_repo", sa.String(length=140), nullable=True),
        sa.Column("deploy_workflow", sa.String(length=100), nullable=True),
        sa.Column("deploy_branch", sa.String(length=255), nullable=True),
        sa.Column("ingest_key_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("updated_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "health_state IN ('unknown', 'healthy', 'degraded', 'unavailable')",
            name=op.f("ck_applications_health_state"),
        ),
        sa.CheckConstraint(
            "failure_threshold BETWEEN 1 AND 5", name=op.f("ck_applications_failure_threshold")
        ),
        sa.CheckConstraint(
            "interval_seconds BETWEEN 15 AND 3600", name=op.f("ck_applications_interval_seconds")
        ),
        sa.CheckConstraint(
            "recovery_threshold BETWEEN 1 AND 5", name=op.f("ck_applications_recovery_threshold")
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 30", name=op.f("ck_applications_timeout_seconds")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint("ingest_key_hash", name=op.f("uq_applications_ingest_key_hash")),
        sa.UniqueConstraint("slug", name=op.f("uq_applications_slug")),
    )
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.create_index("ix_applications_next_check_at", ["next_check_at"], unique=False)

    op.create_table(
        "operator_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("expires_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_operator_sessions_token_hash")),
    )
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_state")),
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=True),
        sa.Column("workflow_name", sa.String(length=100), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("trigger", sa.String(length=40), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("queued_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("started_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("completed_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("synced_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('queued', 'in_progress', 'succeeded', 'failed', 'cancelled', 'needs_attention')",
            name=op.f("ck_deployments_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_deployments_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
        sa.UniqueConstraint(
            "application_id",
            "source",
            "external_id",
            "attempt",
            name=op.f("uq_deployments_application_id_source_external_id_attempt"),
        ),
    )
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_index(
            "ix_deployments_application_queued", ["application_id", "queued_at"], unique=False
        )

    op.create_table(
        "health_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("started_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("signal", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("resolved_ip", sa.String(length=45), nullable=True),
        sa.Column("state_after", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "signal IN ('ok', 'warn', 'fail', 'inconclusive')", name=op.f("ck_health_checks_signal")
        ),
        sa.CheckConstraint(
            "state_after IN ('unknown', 'healthy', 'degraded', 'unavailable')",
            name=op.f("ck_health_checks_state_after"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_health_checks_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_checks")),
    )
    with op.batch_alter_table("health_checks", schema=None) as batch_op:
        batch_op.create_index(
            "ix_health_checks_application_started", ["application_id", "started_at"], unique=False
        )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("opened_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("closed_at", cloudops.db.types.UTCDateTime(), nullable=True),
        sa.Column("worst_state", sa.String(length=16), nullable=False),
        sa.Column("opening_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "worst_state IN ('degraded', 'unavailable')", name=op.f("ck_incidents_worst_state")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_incidents_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
    )
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.create_index(
            "ix_incidents_application_opened", ["application_id", "opened_at"], unique=False
        )
        batch_op.create_index(
            "uq_incidents_one_open_per_application",
            ["application_id"],
            unique=True,
            sqlite_where=sa.text("closed_at IS NULL"),
        )

    op.create_table(
        "log_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("received_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("severity", sa.SmallInteger(), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=True),
        sa.Column("message", sa.String(length=4096), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("environment", sa.String(length=20), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_log_entries_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_log_entries")),
    )
    with op.batch_alter_table("log_entries", schema=None) as batch_op:
        batch_op.create_index(
            "ix_log_entries_application_timestamp", ["application_id", "timestamp"], unique=False
        )
        batch_op.create_index("ix_log_entries_correlation_id", ["correlation_id"], unique=False)
        batch_op.create_index("ix_log_entries_timestamp", ["timestamp"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", cloudops.db.types.UTCDateTime(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("incident_id", sa.Integer(), nullable=True),
        sa.Column("deployment_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'critical')", name=op.f("ck_events_severity")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_events_application_id_applications"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            name=op.f("fk_events_deployment_id_deployments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_events_incident_id_incidents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_events_application_occurred", ["application_id", "occurred_at"], unique=False
        )
        batch_op.create_index("ix_events_occurred_at", ["occurred_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_occurred_at")
        batch_op.drop_index("ix_events_application_occurred")

    op.drop_table("events")
    with op.batch_alter_table("log_entries", schema=None) as batch_op:
        batch_op.drop_index("ix_log_entries_timestamp")
        batch_op.drop_index("ix_log_entries_correlation_id")
        batch_op.drop_index("ix_log_entries_application_timestamp")

    op.drop_table("log_entries")
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_incidents_one_open_per_application", sqlite_where=sa.text("closed_at IS NULL")
        )
        batch_op.drop_index("ix_incidents_application_opened")

    op.drop_table("incidents")
    with op.batch_alter_table("health_checks", schema=None) as batch_op:
        batch_op.drop_index("ix_health_checks_application_started")

    op.drop_table("health_checks")
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_index("ix_deployments_application_queued")

    op.drop_table("deployments")
    op.drop_table("system_state")
    op.drop_table("operator_sessions")
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_index("ix_applications_next_check_at")

    op.drop_table("applications")
