"""Profiles belong to an artist, and their names only need to be unique within it."""

import pytest

from core.profile_import import artist_for_import
from core.profiles import ProfileValidationError, artist_choices, create_profile, update_profile_settings
from pipeline.cli import _profile_id_by_name
from tests.factories import admin_viewer, make_artist


def test_a_new_profile_belongs_to_the_chosen_artist(session):
    artist = make_artist(session)

    assert create_profile(session, artist.id, "Synman").artist_id == artist.id


def test_a_name_is_refused_twice_within_an_artist(session):
    artist = make_artist(session)
    create_profile(session, artist.id, "Synman")

    with pytest.raises(ProfileValidationError) as error:
        create_profile(session, artist.id, "synman")

    assert "already exists" in error.value.errors["name"]


def test_another_artist_can_reuse_a_name(session):
    create_profile(session, make_artist(session).id, "Main")

    assert create_profile(session, make_artist(session).id, "Main").id is not None


def test_an_unknown_artist_is_refused(session):
    with pytest.raises(ProfileValidationError) as error:
        create_profile(session, 999_999, "Synman")

    assert error.value.errors["artist_id"] == "Choose which artist this profile is for"


def test_renaming_only_checks_names_within_the_artist(session):
    first, second = make_artist(session), make_artist(session)
    create_profile(session, first.id, "Taken")
    other = create_profile(session, second.id, "Other")

    update_profile_settings(session, other.id, "Taken", 20)

    assert other.name == "Taken"


def test_artist_choices_are_sorted_by_name(session):
    make_artist(session, "zeta")
    make_artist(session, "Alpha")

    names = [name for _, name in artist_choices(session, admin_viewer())]

    assert names.index("Alpha") < names.index("zeta")


class TestArtistForImport:
    def test_a_named_artist_is_found_whatever_the_case(self, session):
        artist = make_artist(session, "Synman")

        assert artist_for_import(session, "SYNMAN") == artist.id

    def test_a_new_name_creates_the_artist(self, session):
        artist_id = artist_for_import(session, "  Brand   New  ")

        assert artist_for_import(session, "Brand New") == artist_id

    def test_with_no_name_the_only_artist_is_used(self, session):
        artist = make_artist(session)

        assert artist_for_import(session, None) == artist.id

    def test_with_no_name_and_no_artists_it_says_what_to_do(self, session):
        with pytest.raises(LookupError, match="--artist"):
            artist_for_import(session, None)

    def test_with_no_name_and_several_artists_it_asks_which(self, session):
        make_artist(session)
        make_artist(session)

        with pytest.raises(LookupError, match="more than one artist"):
            artist_for_import(session, None)

    def test_a_blank_name_says_what_to_do(self, session):
        with pytest.raises(LookupError, match="--artist"):
            artist_for_import(session, "   ")

    def test_a_name_over_eighty_characters_says_what_to_do(self, session):
        with pytest.raises(LookupError, match="--artist"):
            artist_for_import(session, "a" * 81)


def test_a_profile_name_shared_by_two_artists_is_ambiguous_on_the_command_line(session):
    create_profile(session, make_artist(session).id, "Main")
    create_profile(session, make_artist(session).id, "Main")

    with pytest.raises(LookupError, match="More than one artist"):
        _profile_id_by_name(session, "main")
