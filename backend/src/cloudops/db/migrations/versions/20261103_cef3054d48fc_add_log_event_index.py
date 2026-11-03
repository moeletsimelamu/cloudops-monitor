"""Index log entries by application, event and time, for request metrics.

Revision ID: cef3054d48fc
Revises: fa05ffb1e657
Create Date: 2026-11-03 19:42:16.743953
"""

from collections.abc import Sequence

from alembic import op

revision: str = "cef3054d48fc"
down_revision: str | None = "fa05ffb1e657"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("log_entries", schema=None) as batch_op:
        batch_op.create_index(
            "ix_log_entries_application_event_timestamp",
            ["application_id", "event", "timestamp"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("log_entries", schema=None) as batch_op:
        batch_op.drop_index("ix_log_entries_application_event_timestamp")
