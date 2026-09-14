"""Briefs: Claude turns a digest entry into a short brief and a suggested angle for the pitch.

The brief should quote what the curator says they want, name the reference artists already on
the playlist and the track that sits closest, and say how to reach them. No test calls Claude:
a fake client returns canned JSON, so these tests check the request and the handling.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from pipeline.briefs import BRIEF_MODEL, BriefError, ClaudeBriefWriter
from pipeline.digest import Brief, BriefRequest

REQUEST = BriefRequest(
    profile_name="Synman",
    playlist_name="Glitch Garden",
    playlist_url="https://open.spotify.com/playlist/" + "2" * 22,
    description="Send one track only, no attachments.",
    curator_name="glitchlists",
    followers=1500,
    size_band="500-2k",
    days_since_last_add=5,
    reference_artists=("Autechre", "Boards of Canada"),
    tracks=(("Glass Weather", "Brittle breaks over warm pads"), ("Low Orbit", "Slow drones")),
    contact_route_type="email",
    contact_value="demos@glitchlists.net",
    contact_note="In the playlist description",
)
ANSWER = {
    "brief": "glitchlists curates Glitch Garden, a 1,500-follower IDM playlist updated five days ago.",
    "suggested_angle": "Open with Glass Weather: its brittle breaks sit right next to Autechre.",
    "closest_track": "Glass Weather",
}


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
            content=[SimpleNamespace(type="text", text=self.reply)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=300),
        )


def writer_with(**kwargs) -> tuple[ClaudeBriefWriter, FakeMessages]:
    messages = FakeMessages(**kwargs)
    return ClaudeBriefWriter(SimpleNamespace(messages=messages)), messages


class TestWritingABrief:
    def test_returns_the_brief_the_angle_and_what_it_cost(self):
        writer, _ = writer_with(reply=json.dumps(ANSWER))

        brief = writer.write(REQUEST)

        assert brief == Brief(
            text=ANSWER["brief"], angle=ANSWER["suggested_angle"], spend_usd=Decimal("0.005")
        )

    def test_claude_gets_everything_a_good_brief_needs(self):
        writer, messages = writer_with(reply=json.dumps(ANSWER))

        writer.write(REQUEST)

        call = messages.calls[0]
        message = call["messages"][0]["content"]
        for expected in (
            "Synman",
            "Glitch Garden",
            "Send one track only, no attachments.",
            "Autechre",
            "Boards of Canada",
            "Glass Weather",
            "Slow drones",
            "demos@glitchlists.net",
            "1,500",
        ):
            assert expected in message
        assert call["model"] == BRIEF_MODEL
        assert call["output_config"]["format"]["type"] == "json_schema"

    def test_an_empty_description_is_said_plainly(self):
        writer, messages = writer_with(reply=json.dumps(ANSWER))

        writer.write(BriefRequest(**{**REQUEST.__dict__, "description": ""}))

        assert "no description" in messages.calls[0]["messages"][0]["content"].lower()


class TestWhenItGoesWrong:
    def test_an_api_failure_is_a_brief_error(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        writer, _ = writer_with(error=anthropic.APIConnectionError(request=request))

        with pytest.raises(BriefError):
            writer.write(REQUEST)

    @pytest.mark.parametrize("reply", ["not json", json.dumps({"brief": "", "suggested_angle": "x"}), "{}"])
    def test_an_unusable_answer_is_a_brief_error(self, reply):
        writer, _ = writer_with(reply=reply)

        with pytest.raises(BriefError):
            writer.write(REQUEST)
