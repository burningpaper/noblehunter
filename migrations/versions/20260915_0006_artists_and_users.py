"""artists own profiles; people belong to artists

Jarred (2026-09-15): other artists will use Noble Hunter for their own promotion, so each person
sees only their own artists' work. Existing profiles move under an artist called "Synman". No
users are created here: ALLOWED_EMAILS lives in Vercel, and a user row is written at sign-in.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_signed_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "artists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artists")),
        sa.UniqueConstraint("name", name=op.f("uq_artists_name")),
    )
    op.create_table(
        "artist_members",
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("added_by", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_artist_members_artist_id_artists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_artist_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("artist_id", "user_id", name=op.f("pk_artist_members")),
    )
    op.create_index(op.f("ix_artist_members_user_id"), "artist_members", ["user_id"])

    # Existing profiles are Jarred's. An empty database (tests, a fresh install) gets no artist.
    op.add_column("profiles", sa.Column("artist_id", sa.Integer(), nullable=True))
    op.execute("INSERT INTO artists (name) SELECT 'Synman' WHERE EXISTS (SELECT 1 FROM profiles)")
    op.execute("UPDATE profiles SET artist_id = (SELECT id FROM artists WHERE name = 'Synman')")
    op.alter_column("profiles", "artist_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_profiles_artist_id_artists"),
        "profiles",
        "artists",
        ["artist_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("uq_profiles_name"), "profiles", type_="unique")
    op.create_unique_constraint(op.f("uq_profiles_artist_id_name"), "profiles", ["artist_id", "name"])


def downgrade() -> None:
    # Fails if two artists have a profile with the same name: rename one first.
    op.drop_constraint(op.f("uq_profiles_artist_id_name"), "profiles", type_="unique")
    op.create_unique_constraint(op.f("uq_profiles_name"), "profiles", ["name"])
    op.drop_constraint(op.f("fk_profiles_artist_id_artists"), "profiles", type_="foreignkey")
    op.drop_column("profiles", "artist_id")
    op.drop_index(op.f("ix_artist_members_user_id"), table_name="artist_members")
    op.drop_table("artist_members")
    op.drop_table("artists")
    op.drop_table("users")
