"""Migration 0007 adds mail without disturbing what's already there."""

from sqlalchemy import text

from tests.migration_helpers import downgrade_to, upgrade_to


def test_existing_profiles_and_outreach_survive(engine):
    try:
        downgrade_to("0006")
        with engine.begin() as connection:
            artist_id = connection.scalar(text("insert into artists (name) values ('Synman') returning id"))
            profile_id = connection.scalar(
                text("insert into profiles (name, artist_id) values ('IDM', :artist) returning id"),
                {"artist": artist_id},
            )

        upgrade_to("head")

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "select mail_account_id, open_conversation_limit, quiet_after_days "
                    "from profiles where id = :id"
                ),
                {"id": profile_id},
            ).one()
        assert row.mail_account_id is None
        assert (row.open_conversation_limit, row.quiet_after_days) == (20, 14)
    finally:
        upgrade_to("head")
        with engine.begin() as connection:
            connection.execute(text("truncate profiles, artists restart identity cascade"))
