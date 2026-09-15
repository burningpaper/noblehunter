"""Members see and create profiles only on their own artists; admins see all of them, grouped by artist."""

import re

import pytest
from sqlalchemy import select

from core.models import Profile
from core.profile_rules import DEFAULT_DIGEST_TARGET, DEFAULT_MIN_FOLLOWERS
from core.profiles import artist_choices, list_profiles
from tests.factories import admin_viewer, make_artist, make_member, make_profile, make_user, member_viewer
from tests.profile_helpers import add_contents
from tests.web_helpers import csrf_token, member_client

HTML = {"accept": "text/html"}


def test_a_member_lists_only_their_artists_profiles(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    make_profile(session, "Own profile", artist=mine)
    make_profile(session, "Their profile", artist=theirs)

    names = [summary.name for summary in list_profiles(session, member_viewer(mine))]

    assert names == ["Own profile"]


def test_admins_list_everything_ordered_by_artist_then_name(session):
    b_artist, a_artist = make_artist(session, "b artist"), make_artist(session, "A artist")
    make_profile(session, "zeta", artist=a_artist)
    make_profile(session, "alpha", artist=b_artist)
    make_profile(session, "Beta", artist=a_artist)

    rows = [
        (summary.artist_name, summary.name)
        for summary in list_profiles(session, admin_viewer())
        if summary.artist_name in ("A artist", "b artist")
    ]

    assert rows == [("A artist", "Beta"), ("A artist", "zeta"), ("b artist", "alpha")]


def test_artist_choices_follow_the_viewer(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")

    assert artist_choices(session, member_viewer(mine)) == [(mine.id, "Mine")]
    assert {(mine.id, "Mine"), (theirs.id, "Theirs")} <= set(artist_choices(session, admin_viewer()))


def a_member_of(session, artist):
    make_member(session, artist, make_user(session, "nik@example.com"))
    return member_client(session, "nik@example.com")


def test_the_profiles_page_hides_other_artists(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    make_profile(session, "Own profile", artist=mine)
    make_profile(session, "Their profile", artist=theirs)

    html = a_member_of(session, mine).get("/profiles", headers=HTML).text

    assert "Own profile" in html
    assert "Their profile" not in html
    assert "Theirs" not in html


def test_admins_see_artist_headings(session):
    make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))

    html = member_client(session, "owner@example.com").get("/profiles", headers=HTML).text

    assert 'class="profile-group__title"' in html
    assert "Theirs" in html


def test_a_member_of_two_artists_sees_both_but_not_a_third(session):
    mine_a, mine_b, theirs = (
        make_artist(session, "First Artist"),
        make_artist(session, "Second Artist"),
        make_artist(session, "Third Artist"),
    )
    make_profile(session, "One profile", artist=mine_a)
    make_profile(session, "Two profile", artist=mine_b)
    make_profile(session, "Three profile", artist=theirs)
    user = make_user(session, "nik@example.com")
    make_member(session, mine_a, user)
    make_member(session, mine_b, user)

    html = member_client(session, "nik@example.com").get("/profiles", headers=HTML).text

    assert "One profile" in html and "Two profile" in html
    assert "Three profile" not in html
    assert "First Artist" in html and "Second Artist" in html
    assert "Third Artist" not in html


def test_a_member_cannot_create_a_profile_on_another_artist(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    client = a_member_of(session, mine)

    response = client.post(
        "/profiles",
        data={"name": "Sneaky", "digest_target": "10", "artist_id": str(theirs.id)},
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 404
    assert session.scalar(select(Profile).where(Profile.name == "Sneaky")) is None


def test_an_admin_creating_a_profile_with_an_out_of_range_artist_id_is_explained(session):
    client = member_client(session, "owner@example.com")

    response = client.post(
        "/profiles",
        data={"name": "Synman", "digest_target": "15", "artist_id": "99999999999"},
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 422
    assert "Choose which artist this profile is for" in response.text


def test_an_admin_creating_a_profile_with_a_missing_in_range_artist_id_is_explained(session):
    client = member_client(session, "owner@example.com")

    response = client.post(
        "/profiles",
        data={"name": "Synman", "digest_target": "15", "artist_id": "999999"},
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 422
    assert "Choose which artist this profile is for" in response.text


def test_a_member_cannot_open_another_artists_profile(session):
    mine = make_artist(session, "Mine")
    theirs = make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))

    assert a_member_of(session, mine).get(f"/profiles/{theirs.id}", headers=HTML).status_code == 404


def test_a_missing_profile_and_another_artists_profile_look_identical(session):
    mine = make_artist(session, "Mine")
    theirs = make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))
    client = a_member_of(session, mine)

    missing = client.get("/profiles/999999", headers=HTML)
    someone_elses = client.get(f"/profiles/{theirs.id}", headers=HTML)

    assert missing.status_code == someone_elses.status_code == 404
    assert missing.text == someone_elses.text


@pytest.mark.parametrize(
    "action,data", [("settings", {"name": "Sneaky", "digest_target": "5"}), ("activate", {}), ("pause", {})]
)
def test_a_member_cannot_change_another_artists_profile(session, action, data):
    mine = make_artist(session, "Mine")
    theirs = add_contents(
        session, make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))
    )
    client = a_member_of(session, mine)

    response = client.post(
        f"/profiles/{theirs.id}/{action}",
        data=data,
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 404
    session.refresh(theirs)
    assert theirs.name == "Their profile"
    assert theirs.digest_target == DEFAULT_DIGEST_TARGET
    assert theirs.min_followers == DEFAULT_MIN_FOLLOWERS
    assert theirs.is_active is False


def test_an_admins_profile_page_shows_the_artist_name(session):
    profile = make_profile(session, "Solo", artist=make_artist(session, "Theirs"))

    html = member_client(session, "owner@example.com").get(f"/profiles/{profile.id}", headers=HTML).text

    assert '<p class="eyebrow">Theirs</p>' in html


def test_a_members_own_profile_page_has_no_redundant_artist_name(session):
    mine = make_artist(session, "Mine")
    profile = make_profile(session, "Solo", artist=mine)

    html = a_member_of(session, mine).get(f"/profiles/{profile.id}", headers=HTML).text

    eyebrow = re.search(r'<p class="eyebrow">([^<]*)</p>', html)
    assert eyebrow is not None
    assert eyebrow.group(1) == "Profile"
