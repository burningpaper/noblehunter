"""app-wide settings: the nightly Claude budget

Jarred (2026-09-14): Claude spend is capped per night, $2 by default, and he sets the cap in the
web app. One row (id 1), created the first time the budget is saved.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "nightly_claude_budget_usd",
            sa.Numeric(precision=8, scale=2),
            server_default="2.00",
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_app_settings_single_row")),
        sa.CheckConstraint(
            "nightly_claude_budget_usd between 0 and 50", name=op.f("ck_app_settings_nightly_claude_budget")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
