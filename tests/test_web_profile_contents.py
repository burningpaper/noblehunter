"""Stage 2d pages: editing a profile's contents in place, with the status panel kept in sync."""

import pytest
from fastapi.testclient import TestClient

from core.profile_contents import add_genre, add_reference_artist, add_search_term
from core.profiles import create_profile, set_profile_active
from tests.profile_helpers import add_contents
from tests.web_helpers import FakeGoogle, csrf_token, sign_in, web_settings
from web.app import create_app
from web.db import get_db

TRACK = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"


@pytest.fixture
def client(session):
    app = create_app(web_settings(), identity_provider=FakeGoogle())

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    test_client = TestClient(app, follow_redirects=False)
    sign_in(test_client)
    return test_client


@pytest.fixture
def profile(session):
    return create_profile(session, "Synman")


def htmx(client: TestClient, method: str, path: str, data: dict | None = None):
    headers = {"x-csrf-token": csrf_token(client), "hx-request": "true"}
    if method == "post":
        return client.post(path, data=data or {}, headers=headers)
    return client.request(method.upper(), path, headers=headers)


class TestDetailPageSections:
    def test_every_section_is_on_the_page(self, client, profile):
        html = client.get(f"/profiles/{profile.id}", headers={"accept": "text/html"}).text

        for heading in ("Genres", "Reference artists", "Anti-signals", "Tracks", "Search terms"):
            assert heading in html
        for path in ("genres", "artists", "anti-signals", "tracks", "terms"):
            assert f'hx-post="/profiles/{profile.id}/{path}"' in html

    def test_existing_contents_are_listed_and_escaped(self, client, session, profile):
        add_reference_artist(session, profile.id, "<b>Autechre</b>")

        html = client.get(f"/profiles/{profile.id}", headers={"accept": "text/html"}).text

        assert "&lt;b&gt;Autechre&lt;/b&gt;" in html
        assert "<b>Autechre</b>" not in html


class TestAddingThings:
    def test_adding_a_genre_returns_the_section_and_a_fresh_status_panel(self, client, profile):
        response = htmx(client, "post", f"/profiles/{profile.id}/genres", {"tag": "IDM"})

        assert response.status_code == 200
        assert "IDM" in response.text
        assert 'id="profile-status"' in response.text
        assert 'hx-swap-oob="true"' in response.text

    def test_duplicate_genre_shows_the_error_in_place(self, client, session, profile):
        add_genre(session, profile.id, "IDM")

        response = htmx(client, "post", f"/profiles/{profile.id}/genres", {"tag": "idm"})

        assert response.status_code == 422
        assert "already" in response.text

    def test_adding_a_reference_artist(self, client, profile):
        response = htmx(client, "post", f"/profiles/{profile.id}/artists", {"name": "Boards of Canada"})

        assert response.status_code == 200
        assert "Boards of Canada" in response.text

    def test_adding_anti_signals_of_both_kinds(self, client, profile):
        htmx(client, "post", f"/profiles/{profile.id}/anti-signals", {"kind": "artist", "value": "Lofi Girl"})
        response = htmx(
            client, "post", f"/profiles/{profile.id}/anti-signals", {"kind": "term", "value": "EDM"}
        )

        assert response.status_code == 200
        assert "Lofi Girl" in response.text and "EDM" in response.text

    def test_bad_track_link_is_explained(self, client, profile):
        response = htmx(
            client,
            "post",
            f"/profiles/{profile.id}/tracks",
            {"title": "Glass Weather", "spotify_url": "nope"},
        )

        assert response.status_code == 422
        assert "open.spotify.com/track" in response.text
        assert 'value="Glass Weather"' in response.text

    def test_adding_a_track(self, client, profile):
        response = htmx(
            client,
            "post",
            f"/profiles/{profile.id}/tracks",
            {"title": "Glass Weather", "spotify_url": f"{TRACK}?si=x", "description": "Brittle breaks."},
        )

        assert response.status_code == 200
        assert "Glass Weather" in response.text
        assert TRACK in response.text

    def test_adding_a_search_term_updates_the_nightly_estimate(self, client, profile):
        response = htmx(client, "post", f"/profiles/{profile.id}/terms", {"term": "glitchy ambient"})

        assert response.status_code == 200
        assert "glitchy ambient" in response.text
        assert "5 search requests per night" in response.text


class TestChangingAndRemoving:
    def test_moving_a_genre(self, client, session, profile):
        add_genre(session, profile.id, "IDM")
        ambient = add_genre(session, profile.id, "ambient")

        response = htmx(
            client, "post", f"/profiles/{profile.id}/genres/{ambient.id}/move", {"direction": "up"}
        )

        assert response.status_code == 200
        assert response.text.index("ambient") < response.text.index("IDM")

    def test_removing_a_genre(self, client, session, profile):
        genre = add_genre(session, profile.id, "IDM")

        response = htmx(client, "delete", f"/profiles/{profile.id}/genres/{genre.id}")

        assert response.status_code == 200
        assert "IDM" not in response.text

    def test_pausing_a_search_term(self, client, session, profile):
        term = add_search_term(session, profile.id, "glitchy ambient")

        response = htmx(
            client, "post", f"/profiles/{profile.id}/terms/{term.id}/status", {"status": "paused"}
        )

        assert response.status_code == 200
        assert "Resume" in response.text

    def test_removal_that_breaks_readiness_says_the_profile_was_paused(self, client, session):
        profile = add_contents(session, create_profile(session, "Live One"))
        set_profile_active(session, profile.id, True)
        artist = profile.reference_artists[0]

        response = htmx(client, "delete", f"/profiles/{profile.id}/artists/{artist.id}")

        assert response.status_code == 200
        assert "was paused" in response.text
        assert "Paused" in response.text
        session.refresh(profile)
        assert profile.is_active is False

    def test_another_profiles_item_is_not_found(self, client, session, profile):
        other = create_profile(session, "Side Project")
        genre = add_genre(session, other.id, "IDM")

        response = htmx(client, "delete", f"/profiles/{profile.id}/genres/{genre.id}")

        assert response.status_code == 404

    def test_deleting_without_csrf_is_refused(self, client, session, profile):
        genre = add_genre(session, profile.id, "IDM")

        response = client.delete(f"/profiles/{profile.id}/genres/{genre.id}")

        assert response.status_code == 403
