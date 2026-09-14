"""Fit without reference artists, part two: Claude judges whether a playlist's sound suits the profile.

Asked only about alive, real playlists that have none of the profile's reference artists and don't
name one of its genres. Claude sees what a curator would see at a glance, the most frequent
artists, the name and the description, alongside the profile. No test here calls Claude: a fake
client returns canned JSON.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from pipeline.fit_judge import FIT_MODEL, MAX_ARTISTS_SHOWN, ClaudeFitJudge, FitJudgeError, FitOpinion
from pipeline.qualify import ProfileRules
from tests.test_qualify import playlist, track

RULES = ProfileRules(
    profile_id=1,
    name="Synman",
    genres=("IDM", "glitch"),
    reference_artists=("Autechre", "Boards of Canada"),
    anti_artists=("Lofi Girl",),
    anti_terms=("EDM",),
)
ANSWER = {
    "fits": True,
    "genre_tags": ["glitch", "ambient electronica"],
    "reason": "Brittle, melodic electronics in the same room as Autechre.",
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
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )


def judge_with(**kwargs) -> tuple[ClaudeFitJudge, FakeMessages]:
    messages = FakeMessages(**kwargs)
    return ClaudeFitJudge(SimpleNamespace(messages=messages)), messages


def test_returns_the_opinion_and_what_it_cost():
    judge, _ = judge_with(reply=json.dumps(ANSWER))

    opinion = judge.judge(playlist(name="Night Circuits"), RULES)

    assert opinion == FitOpinion(
        fits=True,
        genre_tags=("glitch", "ambient electronica"),
        reason=ANSWER["reason"],
        spend_usd=Decimal("0.003"),
    )


def test_claude_sees_the_playlist_and_the_profile():
    tracks = [
        track("Mouse on Mars", days_ago=5, uid="a"),
        track("Mouse on Mars", days_ago=6, uid="b"),
        track("Arovane", days_ago=7, uid="c"),
    ]
    judge, messages = judge_with(reply=json.dumps(ANSWER))

    judge.judge(
        playlist(name="Night Circuits", description_text="Warm machines after dark", tracks=tracks), RULES
    )

    call = messages.calls[0]
    message = call["messages"][0]["content"]
    for expected in (
        "Synman",
        "Night Circuits",
        "Warm machines after dark",
        "Mouse on Mars (2)",
        "Arovane",
        "IDM",
        "glitch",
        "Autechre",
        "Boards of Canada",
        "Lofi Girl",
        "EDM",
    ):
        assert expected in message
    assert message.index("Mouse on Mars") < message.index("Arovane")
    assert call["model"] == FIT_MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_only_the_most_frequent_artists_are_shown():
    tracks = [track(f"Artist {n:02d}", days_ago=5, uid=str(n)) for n in range(MAX_ARTISTS_SHOWN + 10)]
    judge, messages = judge_with(reply=json.dumps(ANSWER))

    judge.judge(playlist(tracks=tracks), RULES)

    message = messages.calls[0]["messages"][0]["content"]
    assert "Artist 00" in message
    assert f"Artist {MAX_ARTISTS_SHOWN + 9:02d}" not in message


def test_an_api_failure_is_a_fit_judge_error():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    judge, _ = judge_with(error=anthropic.APIConnectionError(request=request))

    with pytest.raises(FitJudgeError):
        judge.judge(playlist(), RULES)


@pytest.mark.parametrize(
    "reply", ["not json", "{}", json.dumps({"fits": "yes", "genre_tags": [], "reason": "sure"})]
)
def test_an_unusable_answer_is_a_fit_judge_error(reply):
    judge, _ = judge_with(reply=reply)

    with pytest.raises(FitJudgeError):
        judge.judge(playlist(), RULES)
