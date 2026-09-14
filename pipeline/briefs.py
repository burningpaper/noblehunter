"""Briefs: Claude turns a digest entry into something Jarred can pitch from in a minute.

The spec's shape, in 80 to 120 words: who the curator appears to be; what the playlist is (its
sound in the curator's own terms, its size, how active it is); why this music fits (the
reference artists already on it and the closest of the profile's tracks); and how to reach
them, including anything they've said about how they want submissions. Where the curator's
description says what they want, the brief quotes or closely paraphrases it, because that's
the line that makes a pitch land. A one-sentence suggested angle comes with it.

It's one structured-output call with thinking off and low effort, costing a fraction of a
cent per entry. Anything unusable is raised as `BriefError`, and the digest then falls back
to a plain brief.
"""

import json

import anthropic

from pipeline.digest import Brief, BriefRequest
from pipeline.llm_costs import cost_of

BRIEF_MODEL = "claude-sonnet-5"
MAX_OUTPUT_TOKENS = 1_000
REQUEST_TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You write short briefs that help a musician pitch their music to a Spotify playlist \
curator by hand.

Write the brief in 80 to 120 words, covering in order:
1. Who the curator appears to be, in one sentence.
2. What the playlist is: its sound in the curator's own terms, its size and how active it is.
3. Why this music fits: the reference artists already on it, and the one track from the musician's \
list that sits closest, with a few words on why.
4. How to reach them: the route and contact, plus any submission instructions the curator has stated \
(for example "one track only" or "no attachments").

If the curator's description says anything about what they want, quote it or paraphrase it closely. \
Use only the details given; never invent facts. Write plainly and specifically, without hype.

Also write a one-sentence suggested angle for opening the pitch, and name the closest track exactly \
as it's listed."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "suggested_angle": {"type": "string"},
        "closest_track": {"type": "string"},
    },
    "required": ["brief", "suggested_angle", "closest_track"],
    "additionalProperties": False,
}


class BriefError(RuntimeError):
    """No usable brief this time. The digest uses a plain one instead."""


class ClaudeBriefWriter:
    def __init__(self, client, model: str = BRIEF_MODEL):
        self.client = client
        self.model = model

    @classmethod
    def from_api_key(cls, api_key: str, model: str = BRIEF_MODEL) -> "ClaudeBriefWriter":
        return cls(
            anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1), model
        )

    def write(self, request: BriefRequest) -> Brief:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
                messages=[{"role": "user", "content": _details(request)}],
            )
        except anthropic.APIError as error:
            raise BriefError(f"Claude couldn't write the brief ({type(error).__name__})") from error

        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            answer = json.loads(text)
            brief = str(answer["brief"]).strip()
            angle = str(answer["suggested_angle"]).strip()
        except (ValueError, KeyError, TypeError) as error:
            raise BriefError("Claude's brief couldn't be read") from error
        if not brief:
            raise BriefError("Claude returned an empty brief")
        return Brief(text=brief, angle=angle, spend_usd=cost_of(self.model, response.usage))


def _details(request: BriefRequest) -> str:
    size = f"{request.followers:,} followers" if request.followers is not None else "follower count unknown"
    if request.size_band:
        size += f" ({request.size_band})"
    if request.days_since_last_add is None:
        activity = "unknown"
    else:
        activity = f"last track added {request.days_since_last_add} days ago"
    tracks = "\n".join(
        f"- “{title}” (sounds like: {description})" if description else f"- “{title}”"
        for title, description in request.tracks
    )
    contact = f"{request.contact_route_type} {request.contact_value}"
    if request.contact_note:
        contact += f" ({request.contact_note})"
    return "\n".join(
        [
            f"The musician's project: {request.profile_name}",
            f"Playlist: {request.playlist_name} ({request.playlist_url})",
            f"Curator: {request.curator_name or 'unknown'}",
            f"Size: {size}",
            f"Activity: {activity}",
            f"Curator's description: {request.description or 'No description.'}",
            f"Reference artists already on the playlist: {', '.join(request.reference_artists) or 'none'}",
            "The musician's tracks:",
            tracks or "- (none listed)",
            f"Contact: {contact}",
        ]
    )
