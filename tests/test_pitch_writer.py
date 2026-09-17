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


class TestTheSubjectLine:
    # A real pitch was about to go to a real curator titled "Human Error cdot for Neoclassical
    # Music Gems". The subject is the line that decides whether a cold email gets opened, and it
    # is the one line nobody rereads, so a typesetting artifact there costs more than its size.

    def test_a_bare_latex_artifact_is_dropped(self):
        # No backslash anywhere -- the model wrote the command name as a word. This is the shape
        # that actually escaped: strict json.loads would have refused an unescaped backslash.
        answer = json.dumps({"subject": "Human Error cdot for Neoclassical Music Gems", "body": "Hi Nina,"})
        writer, _messages = writer_with(answer)

        assert writer.write(REQUEST).subject == "Human Error for Neoclassical Music Gems"

    def test_a_real_backslash_command_is_dropped_too(self):
        # The model escaped the backslash, so this is valid JSON and a genuine "\cdot" arrives.
        answer = json.dumps({"subject": "Human Error \\cdot for Neoclassical Music Gems", "body": "Hi Nina,"})
        writer, _messages = writer_with(answer)

        assert writer.write(REQUEST).subject == "Human Error for Neoclassical Music Gems"

    def test_a_track_whose_title_contains_a_denylisted_word_survives(self):
        # The guard must not be worse than the bug. "Bullet" here is half a title, not a stray
        # command name, and a subject that loses the track's name is a worse email than one
        # carrying a stray word.
        answer = json.dumps({"subject": "Bullet Train for Broken Machines", "body": "Hi Nina,"})
        writer, _messages = writer_with(answer)

        assert writer.write(REQUEST).subject == "Bullet Train for Broken Machines"

    def test_a_track_called_quad_survives_too(self):
        answer = json.dumps({"subject": "Quad for Broken Machines", "body": "Hi Nina,"})
        writer, _messages = writer_with(answer)

        assert writer.write(REQUEST).subject == "Quad for Broken Machines"

    def test_dropping_an_artifact_leaves_no_double_space(self):
        answer = json.dumps({"subject": "Kelvin mdash Broken Machines", "body": "Hi Nina,"})
        writer, _messages = writer_with(answer)

        subject = writer.write(REQUEST).subject

        assert subject == "Kelvin Broken Machines"
        assert "  " not in subject

    def test_the_body_is_left_exactly_as_claude_wrote_it(self):
        # The body is long-form prose the musician reads and edits before anything is sent, so a
        # stray word there is visible and harmless. Quietly deleting from someone's draft is the
        # worse failure, so the guard stops at the subject.
        body = "Hi Nina,\n\nKelvin cdot sits next to Autechre.\n\nJarred"
        answer = json.dumps({"subject": "Kelvin for Broken Machines", "body": body})
        writer, _messages = writer_with(answer)

        assert writer.write(REQUEST).body == body


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
