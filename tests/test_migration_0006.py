"""Migration 0006 moves existing profiles under an artist called "Synman", and adds nothing to an
empty database."""

from sqlalchemy import text

from tests.migration_helpers import migrate_to


def test_existing_profiles_move_under_synman(engine):
    try:
        migrate_to("0005")
        with engine.begin() as connection:
            connection.execute(text("insert into profiles (name) values ('Legacy IDM')"))

        migrate_to("0006")

        with engine.connect() as connection:
            artist = connection.execute(text("select id, name from artists")).one()
            owner = connection.scalar(text("select artist_id from profiles where name = 'Legacy IDM'"))
        assert artist.name == "Synman"
        assert owner == artist.id
    finally:
        with engine.begin() as connection:
            connection.execute(text("delete from profiles where name = 'Legacy IDM'"))
            connection.execute(text("delete from artists where name = 'Synman'"))
        migrate_to("head")


def test_an_empty_database_gets_no_artist(engine):
    try:
        migrate_to("0005")
        migrate_to("0006")

        with engine.connect() as connection:
            assert connection.scalar(text("select count(*) from artists")) == 0
    finally:
        migrate_to("head")
