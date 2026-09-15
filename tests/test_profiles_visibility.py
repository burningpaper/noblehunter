"""Members see and create profiles only on their own artists; admins see all of them, grouped by artist."""

from sqlalchemy import select

from core.models import Profile
from core.profiles import artist_choices, list_profiles
from tests.factories import admin_viewer, make_artist, make_member, make_profile, make_user, member_viewer
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


def test_a_member_cannot_open_another_artists_profile(session):
    mine = make_artist(session, "Mine")
    theirs = make_profile(session, "Their profile", artist=make_artist(session, "Theirs"))

    assert a_member_of(session, mine).get(f"/profiles/{theirs.id}", headers=HTML).status_code == 404


def test_an_admins_profile_page_shows_the_artist_name(session):
    profile = make_profile(session, "Solo", artist=make_artist(session, "Theirs"))

    html = member_client(session, "owner@example.com").get(f"/profiles/{profile.id}", headers=HTML).text

    assert '<p class="eyebrow">Theirs</p>' in html


def test_a_members_own_profile_page_has_no_redundant_artist_name(session):
    mine = make_artist(session, "Mine")
    profile = make_profile(session, "Solo", artist=mine)

    html = a_member_of(session, mine).get(f"/profiles/{profile.id}", headers=HTML).text

    assert '<p class="eyebrow">Profile</p>' in html
    assert "Mine" not in html
