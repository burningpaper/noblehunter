"""Fit without reference artists, part two: Claude judges whether a playlist's sound suits a profile.

Evaluation asks only about playlists nothing cheaper could place: alive, real, none of the
profile's reference artists on them, none of its genres named, no anti-signal. Claude sees what a
curator would take in at a glance (the most frequent artists, the name, the description) next to
the profile, and says whether the playlist is the same sound world, with a few genre tags and a
reason.

It's one structured-output call with thinking off and low effort, costing a fraction of a cent.
Anything unusable is raised as `FitJudgeError`, and the playlist then stays "no fit".
"""

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import anthropic

from pipeline.llm_costs import cost_of
from pipeline.qualify import ProfileRules
from pipeline.spotify import PlaylistData

FIT_MODEL = "claude-sonnet-5"
MAX_ARTISTS_SHOWN = 40
MAX_GENRE_TAGS = 5
MAX_REASON_LENGTH = 300
MAX_OUTPUT_TOKENS = 400
REQUEST_TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You help a musician decide whether a Spotify playlist is a good place to pitch their \
music. None of the musician's reference artists are on this playlist and it doesn't name one of their \
genres, so judge its sound from the artists on it, its name and its description.

Say it fits only if someone who loves the reference artists would feel at home in this playlist: the \
same sound world, not just a broad umbrella like "electronic" or "chill". If it contains or describes \
anything in the anti-signals, it doesn't fit.

Give up to five genre tags, in the curator's own terms where possible, and a one-sentence reason."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "fits": {"type": "boolean"},
        "genre_tags": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["fits", "genre_tags", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class FitOpinion:
    fits: bool
    genre_tags: tuple[str, ...]
    reason: str
    spend_usd: Decimal = Decimal(0)


class FitJudge(Protocol):
    def judge(self, data: PlaylistData, rules: ProfileRules) -> FitOpinion: ...


class FitJudgeError(RuntimeError):
    """No usable judgement this time. The playlist stays "no fit"."""


class ClaudeFitJudge:
    def __init__(self, client, model: str = FIT_MODEL):
        self.client = client
        self.model = model

    @classmethod
    def from_api_key(cls, api_key: str, model: str = FIT_MODEL) -> "ClaudeFitJudge":
        return cls(
            anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1), model
        )

    def judge(self, data: PlaylistData, rules: ProfileRules) -> FitOpinion:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
                messages=[{"role": "user", "content": _details(data, rules)}],
            )
        except anthropic.APIError as error:
            raise FitJudgeError(f"Claude couldn't judge the fit ({type(error).__name__})") from error

        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            answer = json.loads(text)
            fits, tags, reason = answer["fits"], answer["genre_tags"], answer["reason"]
        except (ValueError, KeyError, TypeError) as error:
            raise FitJudgeError("Claude's fit judgement couldn't be read") from error
        if not isinstance(fits, bool) or not isinstance(tags, list):
            raise FitJudgeError("Claude's fit judgement had an unexpected shape")

        clean_tags = tuple(str(tag).strip() for tag in tags if str(tag).strip())[:MAX_GENRE_TAGS]
        return FitOpinion(
            fits=fits,
            genre_tags=clean_tags,
            reason=str(reason).strip()[:MAX_REASON_LENGTH],
            spend_usd=cost_of(self.model, response.usage),
        )


def _details(data: PlaylistData, rules: ProfileRules) -> str:
    counts = Counter(artist for track in data.tracks for artist in track.artists)
    artists = "; ".join(
        f"{name} ({tracks})" if tracks > 1 else name for name, tracks in counts.most_common(MAX_ARTISTS_SHOWN)
    )
    followers = f"{data.followers:,}" if data.followers is not None else "unknown"
    anti_signals = ", ".join((*rules.anti_artists, *rules.anti_terms))
    reference_artists = ", ".join(rules.reference_artists)
    return "\n".join(
        [
            f"The musician's profile: {rules.name}",
            f"Genres: {', '.join(rules.genres) or 'none listed'}",
            f"Reference artists (music this sits next to): {reference_artists or 'none listed'}",
            f"Anti-signals (the wrong place if present): {anti_signals or 'none'}",
            "",
            f"Playlist: {data.name}",
            f"Description: {data.description_text or '(none)'}",
            f"Followers: {followers}",
            f"Most frequent artists (number of tracks): {artists or 'none listed'}",
        ]
    )
