"""Separate the last conclusive observation from the last check attempt.

Autogenerate proposed dropping last_check_at and adding two columns, which would lose data. The
column is renamed instead (it only ever recorded conclusive checks), and last_attempt_at starts
from the same value.

Revision ID: fa05ffb1e657
Revises: fa400bccf9c8
Create Date: 2026-10-27 10:56:45.787854
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import cloudops.db.types

revision: str = "fa05ffb1e657"
down_revision: str | None = "fa400bccf9c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.alter_column("last_check_at", new_column_name="last_observed_at")
        batch_op.add_column(
            sa.Column("last_attempt_at", cloudops.db.types.UTCDateTime(), nullable=True)
        )
    op.execute("UPDATE applications SET last_attempt_at = last_observed_at")


def downgrade() -> None:
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_column("last_attempt_at")
        batch_op.alter_column("last_observed_at", new_column_name="last_check_at")
