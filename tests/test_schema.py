"""Schema guarantees enforced by Postgres itself, not just by application code."""

from datetime import date, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from core.models import Playlist, RunRequest, SearchTerm
from tests.factories import make_contact, make_curator, make_outreach, make_playlist, make_profile

EXPECTED_TABLES = {
    "profiles",
    "profile_genres",
    "reference_artists",
    "anti_signals",
    "profile_tracks",
    "search_terms",
    "playlists",
    "playlist_sources",
    "playlist_profile_fit",
    "curators",
    "contacts",
    "outreach",
    "runs",
    "run_stage_counts",
    "run_requests",
}
TODAY = date(2026, 9, 14)


def test_migration_creates_every_table(engine):
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_same_curator_cannot_be_digested_twice_within_90_days_across_profiles(session):
    curator = make_curator(session)
    make_outreach(session, curator, make_profile(session), TODAY)

    with pytest.raises(IntegrityError):
        make_outreach(session, curator, make_profile(session), TODAY + timedelta(days=89))


def test_same_curator_cannot_appear_twice_on_the_same_day(session):
    curator = make_curator(session)
    make_outreach(session, curator, make_profile(session), TODAY)

    with pytest.raises(IntegrityError):
        make_outreach(session, curator, make_profile(session), TODAY)


def test_same_curator_allowed_again_from_day_90(session):
    curator = make_curator(session)
    profile = make_profile(session)
    playlist = make_playlist(session, curator=curator)
    make_outreach(session, curator, profile, TODAY, playlist=playlist)

    later = make_outreach(session, curator, profile, TODAY + timedelta(days=90), playlist=playlist)

    assert later.id is not None


def test_different_curators_can_share_a_digest_day(session):
    profile = make_profile(session)
    make_outreach(session, make_curator(session), profile, TODAY)

    assert make_outreach(session, make_curator(session), profile, TODAY).id is not None


def test_contact_keys_are_unique_across_curators(session):
    make_contact(session, make_curator(session), value="curator@label.com")

    with pytest.raises(IntegrityError):
        make_contact(session, make_curator(session), value="Curator@Label.com")


def test_playlist_status_must_be_a_known_value(session):
    session.add(Playlist(spotify_id="1" * 22, name="Bad status", status="bogus"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_search_terms_unique_per_profile_after_normalisation(session):
    profile = make_profile(session)
    session.add(SearchTerm(profile=profile, term="Glitchy Ambient", origin="manual", status="active"))
    session.flush()

    session.add(SearchTerm(profile=profile, term="glitchy  ambient", origin="suggested", status="proposed"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_same_search_term_allowed_in_different_profiles(session):
    for profile in (make_profile(session), make_profile(session)):
        session.add(SearchTerm(profile=profile, term="glitchy ambient", origin="manual", status="active"))

    session.flush()


def test_only_one_open_run_request_for_everything(session):
    session.add(RunRequest(requested_by="burningpaper@gmail.com", status="pending"))
    session.flush()

    session.add(RunRequest(requested_by="burningpaper@gmail.com", status="pending"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_new_run_request_allowed_once_previous_is_done(session):
    first = RunRequest(requested_by="burningpaper@gmail.com", status="pending")
    session.add(first)
    session.flush()
    first.status = "done"
    session.flush()

    session.add(RunRequest(requested_by="burningpaper@gmail.com", status="pending"))
    session.flush()
