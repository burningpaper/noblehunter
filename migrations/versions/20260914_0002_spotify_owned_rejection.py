"""spotify-owned rejection reason

Spotify's own editorial and algorithmic playlists (owners `spotify` and
`thesoundsofspotify`) can never take a pitch, and none of the existing reasons describe
them honestly. Like `not-real`, the new reason is permanent: exclusion never re-checks it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14 03:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_playlists_rejection_reason"
WITH_SPOTIFY_OWNED = "rejection_reason in ('not-alive', 'not-real', 'no-fit', 'spotify-owned')"
WITHOUT_SPOTIFY_OWNED = "rejection_reason in ('not-alive', 'not-real', 'no-fit')"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "playlists", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "playlists", WITH_SPOTIFY_OWNED)


def downgrade() -> None:
    # Keep the rows valid under the old rule; not-real is the other permanent reason.
    op.execute("UPDATE playlists SET rejection_reason = 'not-real' WHERE rejection_reason = 'spotify-owned'")
    op.drop_constraint(op.f(CONSTRAINT), "playlists", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "playlists", WITHOUT_SPOTIFY_OWNED)
