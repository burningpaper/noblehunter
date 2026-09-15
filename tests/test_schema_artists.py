"""Artists own profiles and people belong to artists (Artists and access, stage 1)."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from core.models import ArtistMember, Profile, User
from tests.factories import default_artist_id, make_artist, make_profile, make_user


def test_the_people_tables_exist(engine):
    assert {"users", "artists", "artist_members"} <= set(inspect(engine).get_table_names())


def test_every_profile_belongs_to_an_artist(session):
    session.add(Profile(name="Orphan"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_profile_names_are_unique_within_an_artist(session):
    artist = make_artist(session)
    make_profile(session, "Synman", artist=artist)

    with pytest.raises(IntegrityError):
        make_profile(session, "Synman", artist=artist)


def test_two_artists_can_use_the_same_profile_name(session):
    make_profile(session, "Main", artist=make_artist(session))

    assert make_profile(session, "Main", artist=make_artist(session)).id is not None


def test_artist_names_are_unique(session):
    make_artist(session, "Synman")

    with pytest.raises(IntegrityError):
        make_artist(session, "Synman")


def test_user_emails_are_unique(session):
    make_user(session, "jarred@example.com")

    with pytest.raises(IntegrityError):
        make_user(session, "jarred@example.com")


def test_user_emails_must_be_stored_lowercase(session):
    session.add(User(email="Jarred@Example.com"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_person_joins_an_artist_once(session):
    artist, user = make_artist(session), make_user(session)
    session.add(ArtistMember(artist_id=artist.id, user_id=user.id))
    session.flush()

    session.add(ArtistMember(artist_id=artist.id, user_id=user.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_default_test_artist_is_reused(session):
    assert default_artist_id(session) == default_artist_id(session)
