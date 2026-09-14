"""per-profile follower floor and the too-small rejection reason

Jarred (2026-09-14): playlists under 50 followers aren't worth pitching. The floor is set
per profile (default 50, 0 means none). A playlist that fits but is too small is rejected as
`too-small`, a reason exclusion re-checks after 90 days, because small playlists grow.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REJECTION_CONSTRAINT = "ck_playlists_rejection_reason"
MIN_FOLLOWERS_CONSTRAINT = "ck_profiles_min_followers"
WITH_TOO_SMALL = "rejection_reason in ('not-alive', 'not-real', 'no-fit', 'spotify-owned', 'too-small')"
WITHOUT_TOO_SMALL = "rejection_reason in ('not-alive', 'not-real', 'no-fit', 'spotify-owned')"


def upgrade() -> None:
    op.add_column("profiles", sa.Column("min_followers", sa.Integer(), server_default="50", nullable=False))
    op.create_check_constraint(op.f(MIN_FOLLOWERS_CONSTRAINT), "profiles", "min_followers >= 0")
    op.drop_constraint(op.f(REJECTION_CONSTRAINT), "playlists", type_="check")
    op.create_check_constraint(op.f(REJECTION_CONSTRAINT), "playlists", WITH_TOO_SMALL)


def downgrade() -> None:
    # Keep rows valid under the old rule; too-small playlists become plain no-fit.
    op.execute("UPDATE playlists SET rejection_reason = 'no-fit' WHERE rejection_reason = 'too-small'")
    op.drop_constraint(op.f(REJECTION_CONSTRAINT), "playlists", type_="check")
    op.create_check_constraint(op.f(REJECTION_CONSTRAINT), "playlists", WITHOUT_TOO_SMALL)
    op.drop_constraint(op.f(MIN_FOLLOWERS_CONSTRAINT), "profiles", type_="check")
    op.drop_column("profiles", "min_followers")
