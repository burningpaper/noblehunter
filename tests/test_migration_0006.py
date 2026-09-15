"""Migration 0006 moves existing profiles under an artist called "Synman", and adds nothing to an
empty database."""

import contextlib

from sqlalchemy import text

from tests.migration_helpers import downgrade_to, upgrade_to


def test_existing_profiles_move_under_synman(engine):
    try:
        downgrade_to("0005")
        with engine.begin() as connection:
            connection.execute(text("insert into profiles (name) values ('Legacy IDM')"))

        upgrade_to("0006")

        with engine.connect() as connection:
            artist = connection.execute(text("select id, name from artists")).one()
            owner = connection.scalar(text("select artist_id from profiles where name = 'Legacy IDM'"))
        assert artist.name == "Synman"
        assert owner == artist.id
    finally:
        # Head must come back before anything else: if the upgrade above failed, the database
        # is still on 0005, where `artists` doesn't exist, and a cleanup query would raise and
        # leave the shared test database stuck for every test after this one.
        upgrade_to()
        with contextlib.suppress(Exception):
            with engine.begin() as connection:
                connection.execute(text("truncate profiles, artists restart identity cascade"))


def test_an_empty_database_gets_no_artist(engine):
    try:
        downgrade_to("0005")
        upgrade_to("0006")

        with engine.connect() as connection:
            assert connection.scalar(text("select count(*) from artists")) == 0
    finally:
        upgrade_to()
