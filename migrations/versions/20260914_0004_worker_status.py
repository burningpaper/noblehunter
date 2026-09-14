"""the Mac Mini runner's check-in, and why a "Run now" request failed

The runner writes one row (id 1) every minute and every 30 seconds during a run, so the web
app can tell "runner online" from "Mac asleep or off". A failed "Run now" request now keeps
its reason, so the status panel can say what went wrong.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_status",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("current_run_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_worker_status_single_row")),
        sa.CheckConstraint("state in ('idle', 'running')", name=op.f("ck_worker_status_state")),
        sa.ForeignKeyConstraint(
            ["current_run_id"],
            ["runs.id"],
            name=op.f("fk_worker_status_current_run_id_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_status")),
    )
    op.add_column("run_requests", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_requests", "error")
    op.drop_table("worker_status")
