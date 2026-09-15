"""The People page: admins add people to artists, rename artists and take people off them."""

import pytest
from sqlalchemy import select

from core.models import Artist, ArtistMember, User
from tests.factories import make_artist, make_member, make_profile, make_user
from tests.web_helpers import csrf_token, member_client

HTML = {"accept": "text/html"}


@pytest.fixture
def admin(session):
    return member_client(session, "owner@example.com")


def post(client, path: str, data: dict | None = None):
    return client.post(
        path, data=data or {}, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )


class TestPage:
    def test_admins_see_artists_members_and_profile_counts(self, admin, session):
        artist = make_artist(session, "Synman")
        make_member(session, artist, make_user(session, "nik@example.com"))
        make_profile(session, artist=artist)

        html = admin.get("/people", headers=HTML).text

        assert "Synman" in html
        assert "nik@example.com" in html
        assert "1 profile" in html
        assert "hasn't signed in yet" in html

    def test_admins_get_a_people_link_and_members_do_not(self, admin, session):
        assert 'href="/people"' in admin.get("/", headers=HTML).text

        make_member(session, make_artist(session), make_user(session, "nik@example.com"))
        member = member_client(session, "nik@example.com")
        assert 'href="/people"' not in member.get("/", headers=HTML).text

    def test_members_are_refused(self, session):
        make_member(session, make_artist(session), make_user(session, "nik@example.com"))
        member = member_client(session, "nik@example.com")

        response = member.get("/people", headers=HTML)
        assert response.status_code == 403
        assert "Only admins" in response.text
        assert (
            post(member, "/people/members", {"email": "x@example.com", "new_artist_name": "X"}).status_code
            == 403
        )

    def test_names_are_escaped(self, admin, session):
        make_artist(session, "<b>Loud</b>")

        html = admin.get("/people", headers=HTML).text

        assert "<b>Loud</b>" not in html and "&lt;b&gt;Loud&lt;/b&gt;" in html

    def test_page_structure_for_adding_renaming_and_removing(self, admin, session):
        first = make_artist(session, "Synman")
        make_member(session, first, make_user(session, "nik@example.com"))
        second = make_artist(session, "Nik Beats")

        html = admin.get("/people", headers=HTML).text

        assert 'hx-post="/people/members"' in html
        assert 'hx-target="#people-content"' in html
        assert "hx-confirm=" in html
        assert f'hx-post="/people/artists/{first.id}/rename"' in html
        assert f'hx-post="/people/artists/{second.id}/rename"' in html


class TestAddPerson:
    def test_adding_to_an_existing_artist(self, admin, session):
        artist = make_artist(session, "Synman")

        response = post(admin, "/people/members", {"email": "Nik@Example.com", "artist_id": str(artist.id)})

        assert response.status_code == 200
        assert "Added nik@example.com to Synman" in response.text
        user = session.scalar(select(User).where(User.email == "nik@example.com"))
        assert session.get(ArtistMember, (artist.id, user.id)).added_by == "owner@example.com"

    def test_adding_with_a_new_artist(self, admin, session):
        response = post(
            admin,
            "/people/members",
            {"email": "nik@example.com", "artist_id": "new", "new_artist_name": "Nik Beats"},
        )

        assert response.status_code == 200
        assert session.scalar(select(Artist).where(Artist.name == "Nik Beats")) is not None

    def test_problems_are_shown_in_place_with_the_typed_email(self, admin):
        response = post(admin, "/people/members", {"email": "not-an-email", "artist_id": "new"})

        assert response.status_code == 422
        assert "Enter a valid email address" in response.text
        assert "Choose an artist or name a new one" in response.text
        assert 'value="not-an-email"' in response.text

    def test_a_non_ascii_digit_artist_id_is_a_clean_422_not_a_crash(self, admin):
        response = post(admin, "/people/members", {"email": "nik@example.com", "artist_id": "²"})

        assert response.status_code == 422
        assert "That artist no longer exists" in response.text

    def test_the_email_field_has_a_hint(self, admin):
        html = admin.get("/people", headers=HTML).text

        assert "Use the exact address of their Google account." in html


class TestArtistActions:
    def test_rename(self, admin, session):
        artist = make_artist(session, "Old")

        response = post(admin, f"/people/artists/{artist.id}/rename", {"name": "New"})

        assert response.status_code == 200
        assert session.get(Artist, artist.id).name == "New"

    def test_renaming_to_a_taken_name_is_explained(self, admin, session):
        make_artist(session, "Taken")
        artist = make_artist(session, "Mine")

        response = post(admin, f"/people/artists/{artist.id}/rename", {"name": "taken"})

        assert response.status_code == 422
        # Jinja autoescapes the apostrophe in "There's" to &#39;, so match the escaping-safe part.
        assert "already an artist called" in response.text

    def test_renaming_a_missing_artist_is_404(self, admin):
        assert post(admin, "/people/artists/999999/rename", {"name": "X"}).status_code == 404

    def test_removing_a_member(self, admin, session):
        artist = make_artist(session, "Synman")
        user = make_member(session, artist, make_user(session, "nik@example.com"))

        response = post(admin, f"/people/artists/{artist.id}/members/{user.id}/remove")

        assert response.status_code == 200
        assert "Took nik@example.com off Synman" in response.text
        assert session.get(ArtistMember, (artist.id, user.id)) is None

    def test_removing_someone_not_on_the_artist_is_404(self, admin, session):
        artist, user = make_artist(session), make_user(session)

        assert post(admin, f"/people/artists/{artist.id}/members/{user.id}/remove").status_code == 404
