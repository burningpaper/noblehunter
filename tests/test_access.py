"""Who can see what: members see their own artists' work, admins see everything."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SAWarning

from core.access import (
    AdminOnly,
    NotVisible,
    record_sign_in,
    require_admin,
    require_outreach,
    require_profile,
    viewer_for,
    visible_to,
)
from core.models import ArtistMember, Outreach, Profile, User
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_member,
    make_outreach,
    make_profile,
    make_user,
    member_viewer,
)

ADMINS = frozenset({"owner@example.com"})
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)


class TestViewerFor:
    def test_an_admin_needs_no_membership(self, session):
        viewer = viewer_for(session, "Owner@Example.com", ADMINS)

        assert viewer.is_admin
        assert viewer.email == "owner@example.com"
        assert viewer.artist_ids == frozenset()

    def test_a_member_sees_their_artists(self, session):
        first, second = make_artist(session), make_artist(session)
        user = make_user(session, "nik@example.com")
        make_member(session, first, user)
        make_member(session, second, user)

        viewer = viewer_for(session, "nik@example.com", ADMINS)

        assert not viewer.is_admin
        assert viewer.artist_ids == {first.id, second.id}

    def test_an_admin_who_is_also_a_member_keeps_both(self, session):
        artist = make_artist(session)
        make_member(session, artist, make_user(session, "owner@example.com"))

        viewer = viewer_for(session, "owner@example.com", ADMINS)

        assert viewer.is_admin and viewer.artist_ids == {artist.id}

    @pytest.mark.parametrize("email", ["stranger@example.com", "", "   "])
    def test_anyone_else_gets_no_access(self, session, email):
        assert viewer_for(session, email, ADMINS) is None

    def test_a_person_with_a_user_row_but_no_membership_gets_no_access(self, session):
        make_user(session, "former@example.com")

        assert viewer_for(session, "former@example.com", ADMINS) is None

    def test_removal_takes_effect_on_the_next_check(self, session):
        artist = make_artist(session)
        user = make_member(session, artist, make_user(session, "nik@example.com"))
        assert viewer_for(session, "nik@example.com", ADMINS) is not None

        session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))

        assert viewer_for(session, "nik@example.com", ADMINS) is None

    def test_a_non_ascii_email_gets_no_access(self, session):
        # The Kelvin sign (U+212A) lowercases to plain "k". Without an ASCII check
        # this would collide with an ordinary admin address of the same letters.
        admins = frozenset({"kelvin@example.com"})

        assert viewer_for(session, "Kelvin@example.com", admins) is None

    def test_an_admin_email_with_spacing_and_case_still_matches(self, session):
        viewer = viewer_for(session, "owner@example.com", frozenset({" Owner@Example.com "}))

        assert viewer.is_admin

    def test_a_member_email_with_spacing_and_case_still_matches(self, session):
        artist = make_artist(session)
        make_member(session, artist, make_user(session, "nik@example.com"))

        viewer = viewer_for(session, "  Nik@Example.COM ", ADMINS)

        assert viewer is not None and viewer.artist_ids == {artist.id}


class TestRecordSignIn:
    def test_creates_the_user_on_first_sign_in(self, session):
        record_sign_in(session, email="Nik@Example.com", name="Nik", picture_url=None, now=NOW)

        user = session.scalar(select(User).where(User.email == "nik@example.com"))
        assert (user.name, user.last_signed_in_at) == ("Nik", NOW)

    def test_updates_the_same_user_later_and_keeps_a_known_name(self, session):
        record_sign_in(session, email="nik@example.com", name="Nik", picture_url=None, now=NOW)
        later = datetime(2026, 9, 16, tzinfo=UTC)

        user = record_sign_in(
            session, email="nik@example.com", name=None, picture_url="https://x/p.png", now=later
        )

        assert (user.name, user.picture_url, user.last_signed_in_at) == ("Nik", "https://x/p.png", later)
        assert session.query(User).filter_by(email="nik@example.com").count() == 1

    def test_a_long_name_is_truncated_to_the_column_width(self, session):
        long_name = "N" * 250

        user = record_sign_in(session, email="nik@example.com", name=long_name, picture_url=None, now=NOW)

        assert len(user.name) == 200
        assert user.name == "N" * 200


class TestVisibleTo:
    def test_a_member_only_sees_their_artists_profiles(self, session):
        mine, theirs = make_artist(session), make_artist(session)
        own = make_profile(session, artist=mine)
        make_profile(session, artist=theirs)

        ids = set(
            session.scalars(select(Profile.id).where(visible_to(member_viewer(mine), Profile.artist_id)))
        )

        assert ids == {own.id}

    def test_a_member_of_two_artists_sees_both_but_not_a_third(self, session):
        first, second, third = make_artist(session), make_artist(session), make_artist(session)
        one = make_profile(session, artist=first)
        two = make_profile(session, artist=second)
        make_profile(session, artist=third)

        viewer = member_viewer(first, second)
        ids = set(session.scalars(select(Profile.id).where(visible_to(viewer, Profile.artist_id))))

        assert ids == {one.id, two.id}
        assert require_profile(session, viewer, one.id) is one
        assert require_profile(session, viewer, two.id) is two

    def test_an_admin_sees_every_profile(self, session):
        profiles = {make_profile(session, artist=make_artist(session)).id for _ in range(2)}

        ids = set(session.scalars(select(Profile.id).where(visible_to(admin_viewer(), Profile.artist_id))))

        assert profiles <= ids

    def test_a_viewer_with_no_artists_sees_nothing(self, session):
        make_profile(session, artist=make_artist(session))

        ids = session.scalars(select(Profile.id).where(visible_to(member_viewer(), Profile.artist_id))).all()

        assert ids == []

    def test_forgetting_the_join_raises_instead_of_leaking_every_row(self, session):
        artist = make_artist(session)
        make_profile(session, artist=artist)
        viewer = member_viewer(artist)

        # Outreach doesn't carry artist_id (that's Task 13); filtering it by
        # Profile.artist_id without joining Profile must not silently pass every row.
        with pytest.raises(SAWarning):
            session.scalars(select(Outreach.id).where(visible_to(viewer, Profile.artist_id))).all()


class TestRequire:
    def test_a_member_gets_their_own_profile(self, session):
        artist = make_artist(session)
        profile = make_profile(session, artist=artist)

        assert require_profile(session, member_viewer(artist), profile.id) is profile

    def test_another_artists_profile_is_not_visible(self, session):
        profile = make_profile(session, artist=make_artist(session))

        with pytest.raises(NotVisible):
            require_profile(session, member_viewer(make_artist(session)), profile.id)

    def test_a_missing_profile_is_not_visible_either(self, session):
        with pytest.raises(NotVisible):
            require_profile(session, admin_viewer(), 999_999)

    @pytest.mark.parametrize("profile_id", [0, -1, 2**31])
    def test_an_out_of_range_profile_id_is_not_visible(self, session, profile_id):
        with pytest.raises(NotVisible):
            require_profile(session, admin_viewer(), profile_id)

    def test_an_admin_gets_any_profile(self, session):
        profile = make_profile(session, artist=make_artist(session))

        assert require_profile(session, admin_viewer(), profile.id) is profile

    def test_outreach_follows_its_profiles_artist(self, session):
        artist = make_artist(session)
        outreach = make_outreach(
            session, make_curator(session), make_profile(session, artist=artist), date(2026, 9, 15)
        )

        assert require_outreach(session, member_viewer(artist), outreach.id) is outreach
        with pytest.raises(NotVisible):
            require_outreach(session, member_viewer(make_artist(session)), outreach.id)
        with pytest.raises(NotVisible):
            require_outreach(session, admin_viewer(), 999_999)

    @pytest.mark.parametrize("outreach_id", [0, -1, 2**31])
    def test_an_out_of_range_outreach_id_is_not_visible(self, session, outreach_id):
        with pytest.raises(NotVisible):
            require_outreach(session, admin_viewer(), outreach_id)

    def test_an_admin_gets_any_outreach(self, session):
        artist = make_artist(session)
        outreach = make_outreach(
            session, make_curator(session), make_profile(session, artist=artist), date(2026, 9, 15)
        )

        assert require_outreach(session, admin_viewer(), outreach.id) is outreach

    def test_admin_only_actions(self, session):
        require_admin(admin_viewer())

        with pytest.raises(AdminOnly):
            require_admin(member_viewer(make_artist(session)))
