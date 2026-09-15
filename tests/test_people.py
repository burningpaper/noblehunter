"""Who works on which artist: the rules behind the People page."""

import pytest

from core.models import Artist, ArtistMember, User
from core.people import (
    PeopleValidationError,
    add_member,
    create_artist,
    list_artists,
    normalize_email,
    remove_member,
    rename_artist,
)
from tests.factories import make_artist, make_member, make_profile, make_user

ADMIN = "owner@example.com"


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        "raw", ["  Nik@Example.COM ", "nik@example.com", "nik.o'neil+demos@my-label.co.uk"]
    )
    def test_valid_addresses_are_trimmed_and_lowercased(self, raw):
        assert normalize_email(raw) == raw.strip().lower()

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "nik",
            "nik@",
            "@example.com",
            "nik@example",
            "n ik@example.com",
            "mailto:nik@example.com",
            "nik@example.com.",
            "nik@..com",
            "nik@example.com;",
            '"nik"@example.com',
            "nik​@example.com",
        ],
    )
    def test_invalid_addresses_are_refused(self, raw):
        assert normalize_email(raw) is None

    def test_overlong_addresses_are_refused(self):
        assert normalize_email("a" * 310 + "@example.com") is None


class TestArtists:
    def test_create_tidies_the_name(self, session):
        assert create_artist(session, "  Synman   Live ").name == "Synman Live"

    def test_names_are_unique_whatever_the_case(self, session):
        create_artist(session, "Synman")

        with pytest.raises(PeopleValidationError) as error:
            create_artist(session, "SYNMAN")

        assert error.value.errors["name"] == "There's already an artist called “SYNMAN”"

    @pytest.mark.parametrize("name", ["", "   ", "x" * 81])
    def test_blank_or_overlong_names_are_refused(self, session, name):
        with pytest.raises(PeopleValidationError) as error:
            create_artist(session, name)

        assert "name" in error.value.errors

    def test_rename(self, session):
        artist = make_artist(session, "Old Name")

        rename_artist(session, artist.id, "New Name")

        assert session.get(Artist, artist.id).name == "New Name"

    def test_renaming_to_its_own_name_in_another_case_is_fine(self, session):
        artist = make_artist(session, "synman")

        assert rename_artist(session, artist.id, "Synman").name == "Synman"

    def test_renaming_to_another_artists_name_is_refused(self, session):
        make_artist(session, "Taken")
        artist = make_artist(session, "Mine")

        with pytest.raises(PeopleValidationError):
            rename_artist(session, artist.id, "taken")

    def test_renaming_a_missing_artist_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            rename_artist(session, 999_999, "Anything")

    def test_renaming_an_out_of_range_artist_id_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            rename_artist(session, 99_999_999_999, "Anything")

    def test_list_shows_members_and_profile_counts_sorted(self, session):
        artist = make_artist(session, "b artist")
        make_artist(session, "A artist")
        make_member(session, artist, make_user(session, "zed@example.com"))
        make_member(session, artist, make_user(session, "amy@example.com"))
        make_profile(session, artist=artist)

        summaries = [summary for summary in list_artists(session) if summary.name in ("A artist", "b artist")]

        assert [summary.name for summary in summaries] == ["A artist", "b artist"]
        assert [member.email for member in summaries[1].members] == ["amy@example.com", "zed@example.com"]
        assert summaries[1].profile_count == 1
        assert summaries[0].members == ()


class TestAddMember:
    def test_adds_a_new_person_to_an_existing_artist(self, session):
        artist = make_artist(session)

        user = add_member(session, email="Nik@Example.com", artist_id=artist.id, added_by=ADMIN)

        assert user.email == "nik@example.com"
        membership = session.get(ArtistMember, (artist.id, user.id))
        assert membership.added_by == ADMIN

    def test_creates_the_artist_when_given_a_new_name(self, session):
        user = add_member(
            session, email="nik@example.com", artist_id=None, new_artist_name="Nik Beats", added_by=ADMIN
        )

        artist = session.query(Artist).filter_by(name="Nik Beats").one()
        assert session.get(ArtistMember, (artist.id, user.id)) is not None

    def test_the_same_person_can_join_a_second_artist(self, session):
        first, second = make_artist(session), make_artist(session)
        add_member(session, email="nik@example.com", artist_id=first.id, added_by=ADMIN)

        add_member(session, email="nik@example.com", artist_id=second.id, added_by=ADMIN)

        assert session.query(User).filter_by(email="nik@example.com").count() == 1

    def test_adding_someone_twice_is_explained(self, session):
        artist = make_artist(session, "Synman")
        add_member(session, email="nik@example.com", artist_id=artist.id, added_by=ADMIN)

        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="NIK@example.com", artist_id=artist.id, added_by=ADMIN)

        assert error.value.errors["email"] == "nik@example.com is already on Synman"

    def test_an_invalid_email_and_no_artist_are_both_reported(self, session):
        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="not-an-email", artist_id=None, added_by=ADMIN)

        assert error.value.errors == {
            "email": "Enter a valid email address",
            "artist": "Choose an artist or name a new one",
        }

    def test_an_artist_that_no_longer_exists_is_explained(self, session):
        with pytest.raises(PeopleValidationError) as error:
            add_member(session, email="nik@example.com", artist_id=999_999, added_by=ADMIN)

        assert error.value.errors["artist"] == "That artist no longer exists"

    def test_a_new_artist_name_that_is_taken_is_explained_under_artist(self, session):
        make_artist(session, "Synman")

        with pytest.raises(PeopleValidationError) as error:
            add_member(
                session, email="nik@example.com", artist_id=None, new_artist_name="synman", added_by=ADMIN
            )

        assert error.value.errors["artist"] == "There's already an artist called “synman”"

    def test_a_failed_add_writes_nothing(self, session):
        with pytest.raises(PeopleValidationError):
            add_member(
                session, email="not-an-email", artist_id=None, new_artist_name="Nik Beats", added_by=ADMIN
            )

        assert session.query(Artist).filter_by(name="Nik Beats").first() is None
        assert session.query(User).filter_by(email="not-an-email").first() is None

    def test_whitespace_only_new_artist_name_is_explained(self, session):
        with pytest.raises(PeopleValidationError) as error:
            add_member(
                session, email="nik@example.com", artist_id=None, new_artist_name="   ", added_by=ADMIN
            )

        assert error.value.errors == {"artist": "Choose an artist or name a new one"}

    def test_an_overlong_new_artist_name_is_explained_under_artist(self, session):
        with pytest.raises(PeopleValidationError) as error:
            add_member(
                session, email="nik@example.com", artist_id=None, new_artist_name="x" * 81, added_by=ADMIN
            )

        assert "artist" in error.value.errors

    def test_the_new_artist_membership_records_who_added_it(self, session):
        user = add_member(
            session, email="nik@example.com", artist_id=None, new_artist_name="Nik Beats", added_by=ADMIN
        )

        artist = session.query(Artist).filter_by(name="Nik Beats").one()
        membership = session.get(ArtistMember, (artist.id, user.id))
        assert membership.added_by == ADMIN


class TestRemoveMember:
    def test_removes_the_membership_but_keeps_the_person(self, session):
        artist = make_artist(session)
        user = make_member(session, artist)

        remove_member(session, artist.id, user.id)

        assert session.get(ArtistMember, (artist.id, user.id)) is None
        assert session.get(User, user.id) is not None

    def test_removing_someone_who_is_not_a_member_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            remove_member(session, make_artist(session).id, make_user(session).id)

    def test_removing_with_an_out_of_range_id_raises_lookup_error(self, session):
        with pytest.raises(LookupError):
            remove_member(session, 99_999_999_999, make_user(session).id)
