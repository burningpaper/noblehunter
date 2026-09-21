"""Migration 0008: a playlist can be attributed to Spotify's "Discovered on" section.

The provider column is guarded by a CHECK constraint, so a fourth source is a schema change.
The downgrade tests are the interesting half: attribution can't be rewritten into another
provider honestly, so going back has to refuse rather than guess.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.models import PlaylistSource, SourceProvider
from tests.factories import make_playlist
from tests.migration_helpers import downgrade_to, upgrade_to

PLAYLIST_ID = "9" * 22
CONSTRAINT = "ck_playlist_sources_provider"


def add_source(session, provider: str) -> PlaylistSource:
    source = PlaylistSource(playlist_id=make_playlist(session).spotify_id, provider=provider)
    session.add(source)
    session.flush()
    return source


def constraint_definition(connection) -> str:
    return connection.scalar(
        text("select pg_get_constraintdef(oid) from pg_constraint where conname = :name"),
        {"name": CONSTRAINT},
    )


def test_discovered_on_is_a_valid_provider(session):
    assert add_source(session, SourceProvider.DISCOVERED_ON).provider == "discovered-on"


@pytest.mark.parametrize("provider", ["serper", "brave", "neighbour"])
def test_the_search_providers_and_neighbour_still_work(session, provider):
    assert add_source(session, provider).provider == provider


def test_unknown_providers_are_still_refused(session):
    """`discovered` is deliberately close to the real value: the constraint lists values,
    it doesn't match prefixes."""
    with pytest.raises(IntegrityError):
        add_source(session, "discovered")


def test_downgrading_refuses_while_a_discovered_on_row_exists(engine):
    """No other provider is an honest home for these rows, so 0008 stops and says so."""
    try:
        with engine.begin() as connection:
            connection.execute(
                text("insert into playlists (spotify_id, name) values (:id, 'Found on an artist page')"),
                {"id": PLAYLIST_ID},
            )
            connection.execute(
                text("insert into playlist_sources (playlist_id, provider) values (:id, 'discovered-on')"),
                {"id": PLAYLIST_ID},
            )

        with pytest.raises(RuntimeError, match="discovered-on"):
            downgrade_to("0007")

        # The refusal happens before any DDL, so the database is still at 0008.
        with engine.connect() as connection:
            assert "discovered-on" in constraint_definition(connection)
    finally:
        upgrade_to("head")
        with engine.begin() as connection:
            connection.execute(text("truncate playlists cascade"))


def test_downgrading_narrows_the_constraint_once_those_rows_are_gone(engine):
    try:
        downgrade_to("0007")

        with engine.connect() as connection:
            assert "discovered-on" not in constraint_definition(connection)
    finally:
        upgrade_to("head")

    with engine.connect() as connection:
        assert "discovered-on" in constraint_definition(connection)
