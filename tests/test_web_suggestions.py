"""Ask Claude in the profile editor: ask, tick, add, without leaving the page."""

import json

import pytest
from fastapi.testclient import TestClient

from core.profile_contents import add_reference_artist
from core.profiles import create_profile
from core.suggestions import Suggestion, SuggestionError
from tests.web_helpers import FakeGoogle, csrf_token, sign_in, web_settings
from web.app import create_app
from web.db import get_db


class FakeSuggester:
    def __init__(self, suggestions=(), error: Exception | None = None):
        self.suggestions = list(suggestions)
        self.error = error
        self.calls: list[tuple] = []

    def suggest(self, section, prompt, context):
        self.calls.append((section, prompt, context))
        if self.error:
            raise self.error
        return list(self.suggestions)


@pytest.fixture
def make_client(session):
    def build(suggester=None) -> TestClient:
        app = create_app(web_settings(), identity_provider=FakeGoogle(), suggester=suggester)

        def use_test_session():
            yield session

        app.dependency_overrides[get_db] = use_test_session
        client = TestClient(app, follow_redirects=False)
        sign_in(client)
        return client

    return build


@pytest.fixture
def profile(session):
    return create_profile(session, "Synman")


def post(client: TestClient, path: str, data: dict):
    headers = {"x-csrf-token": csrf_token(client), "hx-request": "true"}
    return client.post(path, data=data, headers=headers)


def choice(value: str, reason: str = "", kind: str | None = None) -> str:
    return json.dumps({"value": value, "reason": reason, "kind": kind})


class TestTheButton:
    def test_offered_for_genres_artists_anti_signals_and_terms_but_not_tracks(self, make_client, profile):
        html = (
            make_client(FakeSuggester()).get(f"/profiles/{profile.id}", headers={"accept": "text/html"}).text
        )

        assert "Ask Claude" in html
        for section in ("genres", "artists", "anti_signals", "terms"):
            assert f'hx-post="/profiles/{profile.id}/suggest/{section}"' in html
        assert "/suggest/tracks" not in html


class TestAsking:
    def test_new_suggestions_come_back_as_ticks_with_reasons(self, make_client, session, profile):
        add_reference_artist(session, profile.id, "Autechre")
        suggester = FakeSuggester(
            [Suggestion("Autechre", "Already there"), Suggestion("Boards of Canada", "Hazy, melodic IDM")]
        )

        response = post(
            make_client(suggester), f"/profiles/{profile.id}/suggest/artists", {"prompt": "more like these"}
        )

        assert response.status_code == 200
        assert 'type="checkbox"' in response.text
        assert "Boards of Canada" in response.text
        assert "Hazy, melodic IDM" in response.text
        assert "Already there" not in response.text
        section, prompt, context = suggester.calls[0]
        assert (section, prompt) == ("artists", "more like these")
        assert context.reference_artists == ("Autechre",)

    def test_claudes_words_are_escaped(self, make_client, profile):
        suggester = FakeSuggester([Suggestion("<img src=x onerror=alert(1)>", "<b>bold</b>")])

        response = post(make_client(suggester), f"/profiles/{profile.id}/suggest/terms", {"prompt": "ideas"})

        assert "<img" not in response.text
        assert "<b>" not in response.text

    def test_an_empty_question_is_refused_without_calling_claude(self, make_client, profile):
        suggester = FakeSuggester()

        response = post(make_client(suggester), f"/profiles/{profile.id}/suggest/terms", {"prompt": "   "})

        assert response.status_code == 422
        assert "Ask Claude something" in response.text
        assert suggester.calls == []

    def test_when_claude_fails_the_message_appears_in_place(self, make_client, profile):
        suggester = FakeSuggester(error=SuggestionError("Claude didn't answer. Try again in a moment."))

        response = post(make_client(suggester), f"/profiles/{profile.id}/suggest/terms", {"prompt": "ideas"})

        assert response.status_code == 200
        assert "Try again in a moment" in response.text

    def test_nothing_new_is_said_plainly(self, make_client, profile):
        response = post(
            make_client(FakeSuggester([])), f"/profiles/{profile.id}/suggest/genres", {"prompt": "ideas"}
        )

        assert response.status_code == 200
        assert "nothing new" in response.text

    def test_without_an_api_key_it_says_what_is_missing(self, make_client, profile):
        response = post(make_client(None), f"/profiles/{profile.id}/suggest/terms", {"prompt": "ideas"})

        assert "ANTHROPIC_API_KEY" in response.text

    def test_sections_without_suggestions_are_not_found(self, make_client, profile):
        response = post(
            make_client(FakeSuggester()), f"/profiles/{profile.id}/suggest/tracks", {"prompt": "x"}
        )

        assert response.status_code == 404


class TestAdding:
    def test_ticked_terms_are_added_and_the_section_and_status_refresh(self, make_client, profile):
        response = post(
            make_client(FakeSuggester()),
            f"/profiles/{profile.id}/suggest/terms/add",
            {"choice": [choice("braindance playlist", "Used in titles"), choice("glitch ambient")]},
        )

        assert response.status_code == 200
        assert "braindance playlist" in response.text
        assert "glitch ambient" in response.text
        assert "Suggested" in response.text
        assert "10 search requests per night" in response.text
        assert 'id="profile-status"' in response.text
        assert "Added 2" in response.text

    def test_anti_signal_kind_survives_the_round_trip(self, make_client, profile):
        post(
            make_client(FakeSuggester()),
            f"/profiles/{profile.id}/suggest/anti_signals/add",
            {"choice": [choice("study beats", kind="term")]},
        )

        assert [(signal.kind, signal.value) for signal in profile.anti_signals] == [("term", "study beats")]

    def test_nothing_ticked_is_explained(self, make_client, profile):
        response = post(make_client(FakeSuggester()), f"/profiles/{profile.id}/suggest/terms/add", {})

        assert response.status_code == 422
        assert "Tick at least one" in response.text

    def test_a_garbled_choice_is_refused(self, make_client, profile):
        response = post(
            make_client(FakeSuggester()),
            f"/profiles/{profile.id}/suggest/terms/add",
            {"choice": ["not json"]},
        )

        assert response.status_code == 422
        assert "Ask again" in response.text
