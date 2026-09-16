"""Claude drafts the pitch, and a plain draft stands in when it can't.

The pitch is Jarred's letter, not Claude's: Claude sees the same brief the digest shows, the
angle the pipeline suggested, and whatever Jarred typed into the box ("shorter", "mention the
Autechre track"), and answers with a subject and a body he then edits before anything is sent.
Nothing here sends: `core.pitches` does that, only when he presses Send.

One structured call, thinking off, bounded output -- the same shape as `core.suggestions` and
`pipeline.fit_judge`. A refusal or an unreadable answer raises `PitchWriterError`, whose text is
safe to show, and the page keeps the draft that was already in the box.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import anthropic

from core.llm_costs import cost_of

logger = logging.getLogger("noble_hunter.pitch_writer")

PITCH_MODEL = "claude-opus-5"
MAX_OUTPUT_TOKENS = 1_500
REQUEST_TIMEOUT_SECONDS = 60
MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 6_000
MAX_INSTRUCTION_LENGTH = 500

SYSTEM_PROMPT = (
    "You help an independent musician write a short email to a Spotify playlist curator, asking them "
    "to listen to one track.\n\n"
    "Write as the musician, in plain English, the way one person writes to another. Be specific about "
    "this playlist: why this track belongs on it, using what the brief says about the playlist and the "
    "artists already on it. No flattery, no hype, no marketing voice, no bullet points, no attachments. "
    "Ask once, politely, and make it easy to say no. Six sentences at most, plus a sign-off.\n\n"
    "Give a subject line under 80 characters that names the track and the playlist. Put the Spotify link "
    "on its own line in the body. Never invent facts about the curator, the playlist or the music."
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PitchRequest:
    artist_name: str
    profile_name: str
    curator_name: str
    playlist_name: str
    playlist_url: str
    brief: str
    angle: str | None
    reference_artists: tuple[str, ...]
    tracks: tuple[tuple[str, str], ...]  # (title, one line on how it sounds)
    sender_name: str
    instruction: str
    previous_body: str


@dataclass(frozen=True)
class Pitch:
    subject: str
    body: str
    spend_usd: Decimal = Decimal(0)


class PitchWriter(Protocol):
    def write(self, request: PitchRequest) -> Pitch: ...


class PitchWriterError(RuntimeError):
    """Claude couldn't draft it this time. The message is safe to show."""


class ClaudePitchWriter:
    def __init__(self, client, model: str = PITCH_MODEL):
        self.client = client
        self.model = model

    @classmethod
    def from_api_key(cls, api_key: str) -> "ClaudePitchWriter":
        return cls(anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1))

    def write(self, request: PitchRequest) -> Pitch:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                thinking={"type": "disabled"},
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
                messages=[{"role": "user", "content": _question(request)}],
            )
        except anthropic.APIError as error:
            logger.warning("Draft a pitch failed: %s", type(error).__name__)
            raise PitchWriterError("Claude didn't answer. Try again in a moment.") from error

        subject, body = _parse(response)
        return Pitch(subject=subject, body=body, spend_usd=cost_of(self.model, response.usage))


def template_pitch(request: PitchRequest) -> Pitch:
    """A plain draft from the facts alone, for when Claude can't write one."""
    greeting = f"Hi {request.curator_name}," if request.curator_name else "Hi there,"
    track_title, track_sound = request.tracks[0] if request.tracks else ("", "")
    track = f"“{track_title}”" if track_title else "a new track"
    lines = [
        greeting,
        "",
        f"I'm {request.sender_name or request.artist_name}, and I make music as {request.artist_name}. "
        f"I came across “{request.playlist_name}” and thought {track} might suit it.",
    ]
    if track_sound:
        lines.append(track_sound.rstrip(".") + ".")
    if request.reference_artists:
        lines.append(f"It sits close to {', '.join(request.reference_artists)}.")
    lines += [
        "",
        request.playlist_url,
        "",
        "No problem at all if it's not right for the playlist -- thanks for listening either way.",
        "",
        request.sender_name or request.artist_name,
    ]
    subject = f"{track_title or request.artist_name} for {request.playlist_name}"
    return Pitch(subject=subject[:MAX_SUBJECT_LENGTH], body="\n".join(lines))


def _question(request: PitchRequest) -> str:
    tracks = "; ".join(f"{title} ({sound})" if sound else title for title, sound in request.tracks)
    parts = [
        f"The musician: {request.artist_name} (profile: {request.profile_name})",
        f"They sign off as: {request.sender_name or request.artist_name}",
        f"Their tracks: {tracks or 'none listed'}",
        f"Artists their music sits next to: {', '.join(request.reference_artists) or 'none listed'}",
        "",
        f"The curator: {request.curator_name or 'name unknown'}",
        f"The playlist: {request.playlist_name}",
        f"Spotify link: {request.playlist_url}",
        f"What we know about it: {request.brief or 'nothing beyond the name'}",
        f"Suggested angle: {request.angle or 'none'}",
    ]
    if request.previous_body.strip():
        parts += [
            "",
            "This is the current draft. Rewrite it, keeping anything the musician clearly wants kept:",
            request.previous_body.strip()[:MAX_BODY_LENGTH],
        ]
    instruction = " ".join(request.instruction.split())[:MAX_INSTRUCTION_LENGTH]
    parts += ["", f"What the musician asked for: {instruction or 'write the first draft'}"]
    return "\n".join(parts)


def _parse(response) -> tuple[str, str]:
    text = next((block.text for block in response.content if block.type == "text"), None)
    try:
        answer = json.loads(text or "")
        subject = " ".join(str(answer["subject"]).split())
        body = str(answer["body"]).strip()
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning(
            "The pitch draft was unreadable (stop_reason=%s)", getattr(response, "stop_reason", None)
        )
        raise PitchWriterError("Claude's draft couldn't be read. Try again.") from error
    if not body or not subject:
        raise PitchWriterError("Claude's draft couldn't be read. Try again.")
    return subject[:MAX_SUBJECT_LENGTH], body[:MAX_BODY_LENGTH]
