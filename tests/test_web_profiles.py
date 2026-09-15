"""Stage 2c: the profiles pages. Routes use the rolled-back test session via a dependency override."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.models import Profile
from core.profiles import create_profile, set_profile_active
from tests.factories import default_artist_id, make_artist
from tests.profile_helpers import add_contents
from tests.web_helpers import FakeGoogle, csrf_token, sign_in, web_settings
from web.app import create_app
from web.db import get_db


@pytest.fixture
def client(session):
    app = create_app(web_settings(), identity_provider=FakeGoogle())

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    test_client = TestClient(app, follow_redirects=False)
    sign_in(test_client)
    return test_client


def htmx_post(client: TestClient, path: str, data: dict | None = None):
    headers = {"x-csrf-token": csrf_token(client), "hx-request": "true"}
    return client.post(path, data=data or {}, headers=headers)


def page(client: TestClient, path: str):
    return client.get(path, headers={"accept": "text/html"})


class TestProfilesList:
    def test_empty_state_invites_the_first_profile(self, client):
        html = page(client, "/profiles").text

        assert "Create your first profile" in html

    def test_lists_profiles_with_their_status(self, client, session):
        live = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
        set_profile_active(session, live.id, True)
        create_profile(session, default_artist_id(session), "Side Project")

        html = page(client, "/profiles").text

        assert "Synman" in html and "Side Project" in html
        assert "Active" in html and "Paused" in html
        assert f'href="/profiles/{live.id}"' in html

    def test_profile_names_are_escaped(self, client, session):
        create_profile(session, default_artist_id(session), "<script>alert(1)</script>")

        html = page(client, "/profiles").text

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_home_links_to_profiles(self, client):
        assert 'href="/profiles"' in page(client, "/").text


class TestCreateProfile:
    def test_creating_a_profile_takes_you_to_it(self, client, session):
        response = htmx_post(
            client,
            "/profiles",
            {"name": "Synman", "digest_target": "15", "artist_id": str(default_artist_id(session))},
        )

        profile = session.scalar(select(Profile).where(Profile.name == "Synman"))
        assert response.status_code == 200
        assert response.headers["hx-redirect"] == f"/profiles/{profile.id}"
        assert profile.digest_target == 15

    def test_invalid_input_rerenders_the_form_with_messages(self, client, session):
        response = htmx_post(
            client,
            "/profiles",
            {"name": "  ", "digest_target": "99", "artist_id": str(default_artist_id(session))},
        )

        assert response.status_code == 422
        assert "Give the profile a name" in response.text
        assert "between 1 and 50" in response.text
        assert 'value="99"' in response.text
        assert session.scalar(select(Profile)) is None

    def test_a_missing_artist_is_explained_in_the_form(self, client, session):
        default_artist_id(session)
        make_artist(session, "Second Artist")

        response = htmx_post(client, "/profiles", {"name": "Synman", "digest_target": "15"})

        assert response.status_code == 422
        assert "Choose which artist this profile is for" in response.text

    def test_creating_without_csrf_token_is_refused(self, client):
        response = client.post("/profiles", data={"name": "Synman", "digest_target": "20"})

        assert response.status_code == 403


class TestProfileDetail:
    def test_shows_settings_and_what_is_missing(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        html = page(client, f"/profiles/{profile.id}").text

        assert 'value="Synman"' in html
        assert "3 reference artists" in html
        assert 'hx-post="/profiles/' in html

    def test_unknown_profile_is_a_friendly_404(self, client):
        response = page(client, "/profiles/999999")

        assert response.status_code == 404
        assert "not found" in response.text.lower()

    def test_saving_settings_updates_and_confirms(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        response = htmx_post(
            client, f"/profiles/{profile.id}/settings", {"name": "Synman Live", "digest_target": "10"}
        )

        assert response.status_code == 200
        assert "Saved" in response.text
        session.refresh(profile)
        assert (profile.name, profile.digest_target) == ("Synman Live", 10)

    def test_invalid_settings_are_rejected_with_messages(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        response = htmx_post(client, f"/profiles/{profile.id}/settings", {"name": "", "digest_target": "10"})

        assert response.status_code == 422
        assert "Give the profile a name" in response.text


class TestActivation:
    def test_activating_an_incomplete_profile_explains_what_is_missing(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        response = htmx_post(client, f"/profiles/{profile.id}/activate")

        assert response.status_code == 422
        assert "reference artists" in response.text
        session.refresh(profile)
        assert profile.is_active is False

    def test_activating_a_complete_profile(self, client, session):
        profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))

        response = htmx_post(client, f"/profiles/{profile.id}/activate")

        assert response.status_code == 200
        assert "Active" in response.text
        session.refresh(profile)
        assert profile.is_active is True

    def test_pausing_a_profile(self, client, session):
        profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
        set_profile_active(session, profile.id, True)

        response = htmx_post(client, f"/profiles/{profile.id}/pause")

        assert response.status_code == 200
        assert "Paused" in response.text
        session.refresh(profile)
        assert profile.is_active is False
