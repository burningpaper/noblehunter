"""Claude drafting a pitch email, and the plain draft used when it can't."""

import json
from dataclasses import replace
from decimal import Decimal

import anthropic
import httpx
import pytest

from core.pitch_writer import (
    ClaudePitchWriter,
    PitchRequest,
    PitchWriterError,
    template_pitch,
)

REQUEST = PitchRequest(
    artist_name="Synman",
    profile_name="IDM Playlists",
    curator_name="Nina",
    playlist_name="Broken Machines",
    playlist_url="https://open.spotify.com/playlist/0000000000000000000001",
    brief="Nina runs Broken Machines (4,120 followers, updated 3 days ago). It already features Autechre.",
    angle="Lead with Kelvin and how it sits next to Autechre.",
    reference_artists=("Autechre", "Boards of Canada"),
    tracks=(("Kelvin", "Broken drums under a warm pad"),),
    sender_name="Jarred",
    instruction="Keep it short and mention the Autechre track.",
    previous_body="",
)


class FakeMessages:
    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _response(self.text)


class FakeClient:
    def __init__(self, messages: FakeMessages):
        self.messages = messages


def _response(text: str):
    class Block:
        type = "text"

    block = Block()
    block.text = text

    class Usage:
        input_tokens = 1_000
        output_tokens = 500
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class Response:
        content = [block]
        usage = Usage()
        stop_reason = "end_turn"

    return Response()


def api_error() -> anthropic.APIError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


def writer_with(text: str = "", error: Exception | None = None) -> tuple[ClaudePitchWriter, FakeMessages]:
    messages = FakeMessages(text, error)
    return ClaudePitchWriter(FakeClient(messages)), messages


class TestAsking:
    def test_it_returns_the_subject_and_body_claude_wrote(self):
        answer = json.dumps({"subject": "Kelvin for Broken Machines", "body": "Hi Nina,\n\nKelvin..."})
        writer, _messages = writer_with(answer)

        pitch = writer.write(REQUEST)

        assert pitch.subject == "Kelvin for Broken Machines"
        assert pitch.body.startswith("Hi Nina,")
        assert pitch.spend_usd > Decimal(0)

    def test_the_call_is_small_and_structured(self):
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(REQUEST)

        sent = messages.calls[0]
        assert sent["thinking"] == {"type": "disabled"}
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["max_tokens"] <= 2_000

    def test_claude_sees_the_brief_the_angle_and_the_instruction(self):
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(REQUEST)

        question = messages.calls[0]["messages"][0]["content"]
        assert "Broken Machines" in question
        assert "Autechre" in question
        assert "Keep it short" in question
        assert "Kelvin" in question

    def test_revising_shows_claude_the_draft_it_is_changing(self):
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(replace(REQUEST, previous_body="Hi Nina, here's my track."))

        question = messages.calls[0]["messages"][0]["content"]
        assert "here's my track" in question
        assert "current draft" in question.lower()

    def test_a_refused_call_raises_something_showable(self):
        writer, _messages = writer_with(error=api_error())

        with pytest.raises(PitchWriterError, match="Claude didn't answer"):
            writer.write(REQUEST)

    def test_an_unreadable_answer_raises_something_showable(self):
        writer, _messages = writer_with("not json")

        with pytest.raises(PitchWriterError, match="couldn't be read"):
            writer.write(REQUEST)

    def test_a_blank_body_is_refused_rather_than_sent(self):
        writer, _messages = writer_with(json.dumps({"subject": "S", "body": "   "}))

        with pytest.raises(PitchWriterError, match="couldn't be read"):
            writer.write(REQUEST)

    def test_a_long_subject_is_trimmed(self):
        writer, _messages = writer_with(json.dumps({"subject": "x" * 500, "body": "B"}))

        assert len(writer.write(REQUEST).subject) <= 200

    def test_a_request_carrying_nothing_still_reaches_claude(self):
        # These fields come from nullable columns. Building the question happens outside the try
        # that makes a PitchWriterError, so a None here would escape as a bare AttributeError.
        writer, messages = writer_with(json.dumps({"subject": "S", "body": "B"}))

        writer.write(replace(REQUEST, angle=None, instruction=None, previous_body=None))

        assert "None" not in messages.calls[0]["messages"][0]["content"]


class TestThePlainDraft:
    def test_it_names_the_playlist_the_curator_and_a_track(self):
        pitch = template_pitch(REQUEST)

        assert "Broken Machines" in pitch.subject
        assert "Nina" in pitch.body
        assert "Kelvin" in pitch.body
        assert "Jarred" in pitch.body
        assert pitch.spend_usd == Decimal(0)

    def test_it_works_with_nothing_but_the_playlist(self):
        bare = PitchRequest(
            artist_name="Synman",
            profile_name="IDM Playlists",
            curator_name="",
            playlist_name="Broken Machines",
            playlist_url="https://open.spotify.com/playlist/0000000000000000000001",
            brief="",
            angle=None,
            reference_artists=(),
            tracks=(),
            sender_name="",
            instruction="",
            previous_body="",
        )

        pitch = template_pitch(bare)

        assert "Broken Machines" in pitch.body
        assert "Hi there" in pitch.body

    def test_it_survives_a_request_full_of_nothing(self):
        # This is the draft used when Claude has already failed. It must not fail too, and a
        # nullable column arriving as None must never print the word "None" at a curator.
        empty = PitchRequest(
            artist_name="Synman",
            profile_name=None,
            curator_name=None,
            playlist_name="Broken Machines",
            playlist_url=None,
            brief=None,
            angle=None,
            reference_artists=(),
            tracks=((None, None),),
            sender_name=None,
            instruction=None,
            previous_body=None,
        )

        pitch = template_pitch(empty)

        assert "Broken Machines" in pitch.body
        assert "Synman" in pitch.body
        assert "None" not in pitch.body
        assert "None" not in pitch.subject
