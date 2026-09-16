"""email pitching: mailboxes, messages, drafts and the conversation ceiling

Jarred (2026-09-16): pitch curators by email from inside the digest and read replies there.
A mailbox belongs to an artist; a profile pitches from one of its artist's mailboxes, which a
composite foreign key enforces. Profiles also gain the conversation-load settings: the digest
tops up to `open_conversation_limit` open conversations, and a pitch stops counting as open
after `quiet_after_days` without an answer.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("history_id", sa.String(length=40), nullable=True),
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("connected_by", sa.String(length=320), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_reconnect", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artists.id"], name=op.f("fk_mail_accounts_artist_id_artists"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_accounts")),
        sa.UniqueConstraint("artist_id", "address", name=op.f("uq_mail_accounts_artist_id_address")),
        # Lets profiles point a composite foreign key here, so a profile can only use its own
        # artist's mailbox.
        sa.UniqueConstraint("id", "artist_id", name=op.f("uq_mail_accounts_id_artist_id")),
    )
    op.create_index(op.f("ix_mail_accounts_artist_id"), "mail_accounts", ["artist_id"])

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mail_account_id", sa.Integer(), nullable=False),
        sa.Column("outreach_id", sa.Integer(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=64), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=64), nullable=False),
        sa.Column("rfc822_message_id", sa.String(length=400), nullable=True),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("from_address", sa.String(length=320), nullable=False),
        sa.Column("to_address", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("quoted_text", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("direction in ('out', 'in')", name=op.f("ck_email_messages_direction")),
        sa.ForeignKeyConstraint(
            ["mail_account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_email_messages_mail_account_id_mail_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outreach_id"],
            ["outreach.id"],
            name=op.f("fk_email_messages_outreach_id_outreach"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_messages")),
        sa.UniqueConstraint(
            "mail_account_id",
            "gmail_message_id",
            name=op.f("uq_email_messages_mail_account_id_gmail_message_id"),
        ),
        sa.UniqueConstraint("send_key", name=op.f("uq_email_messages_send_key")),
    )
    op.create_index(op.f("ix_email_messages_outreach_id"), "email_messages", ["outreach_id"])
    op.create_index(op.f("ix_email_messages_gmail_thread_id"), "email_messages", ["gmail_thread_id"])

    op.add_column("profiles", sa.Column("mail_account_id", sa.Integer(), nullable=True))
    op.add_column(
        "profiles",
        sa.Column("open_conversation_limit", sa.Integer(), server_default="20", nullable=False),
    )
    op.add_column(
        "profiles", sa.Column("quiet_after_days", sa.Integer(), server_default="14", nullable=False)
    )
    op.create_foreign_key(
        op.f("fk_profiles_mail_account_id_mail_accounts"),
        "profiles",
        "mail_accounts",
        ["mail_account_id", "artist_id"],
        ["id", "artist_id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_profiles_open_conversation_limit"), "profiles", "open_conversation_limit between 1 and 200"
    )
    op.create_check_constraint(
        op.f("ck_profiles_quiet_after_days"), "profiles", "quiet_after_days between 1 and 365"
    )

    op.add_column("outreach", sa.Column("mail_account_id", sa.Integer(), nullable=True))
    op.add_column("outreach", sa.Column("gmail_thread_id", sa.String(length=64), nullable=True))
    op.add_column("outreach", sa.Column("draft_subject", sa.Text(), nullable=True))
    op.add_column("outreach", sa.Column("draft_body", sa.Text(), nullable=True))
    op.add_column("outreach", sa.Column("draft_track_id", sa.Integer(), nullable=True))
    op.add_column("outreach", sa.Column("draft_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_outreach_mail_account_id_mail_accounts"),
        "outreach",
        "mail_accounts",
        ["mail_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_outreach_draft_track_id_profile_tracks"),
        "outreach",
        "profile_tracks",
        ["draft_track_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_outreach_mail_account_id_gmail_thread_id"),
        "outreach",
        ["mail_account_id", "gmail_thread_id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_outreach_mail_account_id_gmail_thread_id"), "outreach", type_="unique")
    op.drop_constraint(op.f("fk_outreach_draft_track_id_profile_tracks"), "outreach", type_="foreignkey")
    op.drop_constraint(op.f("fk_outreach_mail_account_id_mail_accounts"), "outreach", type_="foreignkey")
    for column in (
        "draft_updated_at",
        "draft_track_id",
        "draft_body",
        "draft_subject",
        "gmail_thread_id",
        "mail_account_id",
    ):
        op.drop_column("outreach", column)

    op.drop_constraint(op.f("ck_profiles_quiet_after_days"), "profiles", type_="check")
    op.drop_constraint(op.f("ck_profiles_open_conversation_limit"), "profiles", type_="check")
    op.drop_constraint(op.f("fk_profiles_mail_account_id_mail_accounts"), "profiles", type_="foreignkey")
    for column in ("quiet_after_days", "open_conversation_limit", "mail_account_id"):
        op.drop_column("profiles", column)

    op.drop_index(op.f("ix_email_messages_gmail_thread_id"), table_name="email_messages")
    op.drop_index(op.f("ix_email_messages_outreach_id"), table_name="email_messages")
    op.drop_table("email_messages")
    op.drop_index(op.f("ix_mail_accounts_artist_id"), table_name="mail_accounts")
    op.drop_table("mail_accounts")
