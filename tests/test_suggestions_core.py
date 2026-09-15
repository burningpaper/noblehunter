"""Ask Claude, the core: what Claude is told, and what happens to the answers Jarred ticks."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from sqlalchemy import select

from core.models import SearchTerm, SearchTermOrigin, SearchTermStatus
from core.profile_contents import add_anti_signal, add_genre, add_reference_artist, add_search_term
from core.profiles import ProfileValidationError, create_profile
from core.suggestions import (
    DEFAULT_MODEL,
    MAX_PROMPT_LENGTH,
    MAX_SUGGESTIONS,
    ClaudeSuggester,
    Suggestion,
    SuggestionError,
    add_suggestions,
    new_suggestions,
    profile_context,
    validate_prompt,
)
from tests.factories import default_artist_id


@pytest.fixture
def profile(session):
    return create_profile(session, default_artist_id(session), "Synman")


class FakeMessages:
    def __init__(self, reply: str | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)], stop_reason="end_turn"
        )


def fake_client(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(**kwargs))


def reply(*items: dict) -> str:
    return json.dumps({"suggestions": list(items)})


class TestProfileContext:
    def test_lists_everything_the_profile_already_has(self, session, profile):
        add_genre(session, profile.id, "IDM")
        add_reference_artist(session, profile.id, "Autechre")
        add_anti_signal(session, profile.id, "artist", "Lofi Girl")
        add_anti_signal(session, profile.id, "term", "EDM")
        add_search_term(session, profile.id, "glitch playlist")

        context = profile_context(profile)

        assert context.name == "Synman"
        assert context.genres == ("IDM",)
        assert context.reference_artists == ("Autechre",)
        assert context.anti_artists == ("Lofi Girl",)
        assert context.anti_terms == ("EDM",)
        assert context.search_terms == ("glitch playlist",)


class TestValidatePrompt:
    def test_trims_the_question(self):
        assert validate_prompt("  more artists like these  ") == "more artists like these"

    @pytest.mark.parametrize("prompt", ["", "   "])
    def test_an_empty_question_is_refused(self, prompt):
        with pytest.raises(ProfileValidationError) as caught:
            validate_prompt(prompt)
        assert "prompt" in caught.value.errors

    def test_a_very_long_question_is_refused(self):
        with pytest.raises(ProfileValidationError):
            validate_prompt("x" * (MAX_PROMPT_LENGTH + 1))


class TestNewSuggestions:
    def test_drops_what_the_profile_already_has(self, session, profile):
        add_reference_artist(session, profile.id, "Autechre")

        fresh = new_suggestions(
            profile,
            "artists",
            [Suggestion(" AUTECHRE "), Suggestion("Boards of Canada", "Hazy, melodic IDM")],
        )

        assert fresh == [Suggestion("Boards of Canada", "Hazy, melodic IDM")]

    def test_drops_blanks_and_repeats_and_tidies_spacing(self, profile):
        answer = [Suggestion("  glitch   ambient "), Suggestion("Glitch ambient"), Suggestion("  ")]

        assert [s.value for s in new_suggestions(profile, "terms", answer)] == ["glitch ambient"]

    def test_anti_signals_need_a_kind_and_are_compared_within_it(self, session, profile):
        add_anti_signal(session, profile.id, "artist", "Lofi Girl")
        answer = [
            Suggestion("Lofi Girl", kind="artist"),
            Suggestion("lofi girl", kind="term"),
            Suggestion("EDM", kind=None),
            Suggestion("study beats", kind="term"),
        ]

        fresh = new_suggestions(profile, "anti_signals", answer)

        assert [(s.value, s.kind) for s in fresh] == [("lofi girl", "term"), ("study beats", "term")]

    def test_kind_only_matters_for_anti_signals(self, profile):
        assert new_suggestions(profile, "genres", [Suggestion("IDM", kind="term")]) == [Suggestion("IDM")]

    def test_keeps_at_most_the_limit(self, profile):
        many = [Suggestion(f"term number {i}") for i in range(MAX_SUGGESTIONS + 5)]

        assert len(new_suggestions(profile, "terms", many)) == MAX_SUGGESTIONS


class TestAddSuggestions:
    def test_ticked_search_terms_go_live_marked_as_suggested(self, session, profile):
        result = add_suggestions(
            session, profile.id, "terms", [Suggestion("braindance playlist", "Curators use it in titles")]
        )

        term = session.scalars(select(SearchTerm).where(SearchTerm.profile_id == profile.id)).one()
        assert term.term == "braindance playlist"
        assert term.origin == SearchTermOrigin.SUGGESTED
        assert term.status == SearchTermStatus.ACTIVE
        assert term.rationale == "Curators use it in titles"
        assert result.added == ("braindance playlist",)

    def test_genres_join_the_end_of_the_list(self, session, profile):
        add_genre(session, profile.id, "IDM")

        add_suggestions(session, profile.id, "genres", [Suggestion("braindance"), Suggestion("glitch")])

        ordered = sorted(profile.genres, key=lambda genre: genre.priority)
        assert [genre.tag for genre in ordered] == ["IDM", "braindance", "glitch"]

    def test_artists_and_anti_signals(self, session, profile):
        add_suggestions(session, profile.id, "artists", [Suggestion("Boards of Canada")])
        add_suggestions(session, profile.id, "anti_signals", [Suggestion("EDM", kind="term")])

        assert [artist.display_name for artist in profile.reference_artists] == ["Boards of Canada"]
        assert [(signal.kind, signal.value) for signal in profile.anti_signals] == [("term", "EDM")]

    def test_items_already_there_are_skipped_and_the_rest_added(self, session, profile):
        add_reference_artist(session, profile.id, "Autechre")

        result = add_suggestions(
            session, profile.id, "artists", [Suggestion("autechre"), Suggestion("Plaid")]
        )

        assert result.added == ("Plaid",)
        assert result.skipped == ("autechre",)
        assert "Plaid" in result.summary
        assert "autechre" in result.summary

    def test_nothing_ticked_is_explained(self, session, profile):
        with pytest.raises(ProfileValidationError) as caught:
            add_suggestions(session, profile.id, "terms", [])
        assert "choice" in caught.value.errors

    def test_unknown_sections_are_a_programming_error(self, session, profile):
        with pytest.raises(ValueError, match="section"):
            add_suggestions(session, profile.id, "tracks", [Suggestion("x")])


class TestClaudeSuggester:
    def test_asks_about_the_section_with_the_profile_in_view(self, session, profile):
        add_reference_artist(session, profile.id, "Autechre")
        client = fake_client(reply=reply({"value": "Boards of Canada", "reason": "Hazy, melodic IDM"}))

        answer = ClaudeSuggester(client).suggest(
            "artists", "more artists like these", profile_context(profile)
        )

        assert answer == [Suggestion("Boards of Canada", "Hazy, melodic IDM")]
        call = client.messages.calls[0]
        assert call["model"] == DEFAULT_MODEL
        message = call["messages"][0]["content"]
        assert "more artists like these" in message
        assert "Autechre" in message
        assert "Reference artists" in message
        assert call["output_config"]["format"]["type"] == "json_schema"

    def test_anti_signal_answers_carry_their_kind(self, profile):
        client = fake_client(
            reply=reply({"value": "Lofi Girl", "reason": "Study-beats channel", "kind": "artist"})
        )

        answer = ClaudeSuggester(client).suggest("anti_signals", "wrong vibe?", profile_context(profile))

        assert answer == [Suggestion("Lofi Girl", "Study-beats channel", "artist")]

    def test_an_api_failure_becomes_a_friendly_error(self, profile):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        client = fake_client(error=anthropic.APIConnectionError(request=request))

        with pytest.raises(SuggestionError, match="Claude"):
            ClaudeSuggester(client).suggest("terms", "ideas?", profile_context(profile))

    def test_an_unreadable_answer_becomes_a_friendly_error(self, profile):
        with pytest.raises(SuggestionError, match="Claude"):
            ClaudeSuggester(fake_client(reply="not json")).suggest(
                "terms", "ideas?", profile_context(profile)
            )
