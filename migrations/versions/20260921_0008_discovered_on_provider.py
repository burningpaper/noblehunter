"""discovered-on as a fourth playlist source

Web search has run out of new playlists: by 2026-09-17 nearly every search term was returning
about fifty results and zero new playlists, and that night produced one digest entry. A spike on
2026-09-20 read the `discoveredOnV2` section of a logged-out Spotify artist page across the IDM
profile's 18 reference artists and found 96 pitchable playlists, 90 of which five days of web
search had never seen, with 29 of 99 qualifying against the pipeline's own rules. Recording that
source means a fourth value in `playlist_sources.provider`, which is guarded by a CHECK
constraint -- so it is a schema change and not merely an enum value.

Only the constraint widens. No rows are read, moved or rewritten.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-21 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_playlist_sources_provider"
WITH_DISCOVERED_ON = "provider in ('serper', 'brave', 'neighbour', 'discovered-on')"
WITHOUT_DISCOVERED_ON = "provider in ('serper', 'brave', 'neighbour')"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "playlist_sources", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "playlist_sources", WITH_DISCOVERED_ON)


def downgrade() -> None:
    # Migrations 0002 and 0003 could rewrite their rows to a neighbouring value on the way down,
    # because one rejection reason is much like another. This column can't. It records *where a
    # playlist came from*, and a discovered-on row relabelled 'serper' is a lie about provenance --
    # the spike's central finding was that search had never seen 94% of these playlists. Deleting
    # the rows instead would throw the attribution away. Both options quietly corrupt the record,
    # at 3am, in the direction nobody is watching. So this refuses, names the number, and leaves
    # the judgement with whoever is running it: only they know if that history is expendable.
    remaining = op.get_bind().scalar(
        sa.text("select count(*) from playlist_sources where provider = 'discovered-on'")
    )
    if remaining:
        raise RuntimeError(
            f"{remaining} playlist_sources row(s) still record provider 'discovered-on', which the "
            "old constraint rejects. Nothing has been changed. Decide what that attribution is "
            "worth -- delete those rows, or relabel them by hand -- then run this downgrade again."
        )

    op.drop_constraint(op.f(CONSTRAINT), "playlist_sources", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "playlist_sources", WITHOUT_DISCOVERED_ON)
