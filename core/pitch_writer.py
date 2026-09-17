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
import re
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
    "Give a subject line under 80 characters that names the track and the playlist, in ordinary words "
    "and ordinary punctuation -- no LaTeX, no markdown, no typographic control sequences, no special "
    "characters standing in as a separator. Put the Spotify link on its own line in the body. Never "
    "invent facts about the curator, the playlist or the music."
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
    sender_name: str | None  # the sender's own name, if we know one -- see web/pitches.py
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
    artist = _text(request.artist_name)
    signature = _text(request.sender_name) or artist
    greeting = f"Hi {_text(request.curator_name)}," if request.curator_name else "Hi there,"
    track_title, track_sound = request.tracks[0] if request.tracks else ("", "")
    track = f"“{_text(track_title)}”" if track_title else "a new track"
    lines = [
        greeting,
        "",
        f"I'm {signature}, and I make music as {artist}. "
        f"I came across “{_text(request.playlist_name)}” and thought {track} might suit it.",
    ]
    if track_sound:
        lines.append(_text(track_sound).rstrip(".") + ".")
    if request.reference_artists:
        lines.append(f"It sits close to {_join(request.reference_artists)}.")
    lines += [
        "",
        _text(request.playlist_url),
        "",
        "No problem at all if it's not right for the playlist -- thanks for listening either way.",
        "",
        signature,
    ]
    subject = f"{_text(track_title) or artist} for {_text(request.playlist_name)}"
    return Pitch(subject=subject[:MAX_SUBJECT_LENGTH], body="\n".join(lines))


def _question(request: PitchRequest) -> str:
    tracks = "; ".join(
        f"{_text(title)} ({_text(sound)})" if sound else _text(title) for title, sound in request.tracks
    )
    parts = [
        f"The musician: {_text(request.artist_name)} (profile: {_text(request.profile_name)})",
        f"They sign off as: {_text(request.sender_name) or _text(request.artist_name)}",
        f"Their tracks: {tracks or 'none listed'}",
        f"Artists their music sits next to: {_join(request.reference_artists) or 'none listed'}",
        "",
        f"The curator: {_text(request.curator_name) or 'name unknown'}",
        f"The playlist: {_text(request.playlist_name)}",
        f"Spotify link: {_text(request.playlist_url)}",
        f"What we know about it: {_text(request.brief) or 'nothing beyond the name'}",
        f"Suggested angle: {_text(request.angle) or 'none'}",
    ]
    previous = _text(request.previous_body).strip()
    if previous:
        parts += [
            "",
            "This is the current draft. Rewrite it, keeping anything the musician clearly wants kept:",
            previous[:MAX_BODY_LENGTH],
        ]
    instruction = " ".join(_text(request.instruction).split())[:MAX_INSTRUCTION_LENGTH]
    parts += ["", f"What the musician asked for: {instruction or 'write the first draft'}"]
    return "\n".join(parts)


def _text(value: object) -> str:
    """Whatever arrived, as a string.

    Every `PitchRequest` field is typed `str`, and callers should send one. But these two
    functions run *outside* the try that turns trouble into `PitchWriterError`, and one of them
    is the fallback used when Claude has already failed -- so a `None` from a nullable column
    (an unset angle, a track with no description, an empty draft) must not raise here, of all
    places. The caller coercing at its boundary is still the better fix; this is the seatbelt.
    """
    return str(value) if value else ""


def _join(values) -> str:
    return ", ".join(_text(value) for value in values if value)


# Typesetting a model reaches for when it has to join a track to a playlist and nobody told it how.
# The backslash forms are unambiguous evidence, so anything shaped like a command goes, and a lone
# backslash before punctuation loses the backslash and keeps the punctuation ("\&" -> "&").
#
# The bare words are the ones that actually got out, and they are matched in lowercase only. Every
# one is a LaTeX command name, which a model drops in lowercase; a real title capitalises. Matching
# case-insensitively would take the track's name out of "Bullet Train for X" or leave a pitch for a
# track called "Quad" with no track in the subject at all -- worse than the stray word it removes.
_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+")
_LATEX_ESCAPE = re.compile(r"\\(?![a-zA-Z])")
_BARE_ARTIFACT = re.compile(r"\b(?:cdot|bullet|textbar|ndash|mdash|textbullet|quad|hspace)\b")


def _clean_subject(subject: str) -> str:
    """The subject with any typesetting artifact taken out and the gap closed up.

    The subject only. It is the line that decides whether a cold email gets opened and the one line
    nobody rereads, so a stray "cdot" there is worth removing unasked. The body is not touched: it
    is long-form prose the musician reads and edits before anything is sent, so a stray word there
    is visible and harmless, and quietly deleting from someone's draft is the worse failure.
    """
    cleaned = _LATEX_COMMAND.sub(" ", subject)
    cleaned = _LATEX_ESCAPE.sub("", cleaned)
    cleaned = _BARE_ARTIFACT.sub(" ", cleaned)
    return " ".join(cleaned.split())


def _parse(response) -> tuple[str, str]:
    text = next((block.text for block in response.content if block.type == "text"), None)
    try:
        answer = json.loads(text or "")
        subject = _clean_subject(" ".join(str(answer["subject"]).split()))
        body = str(answer["body"]).strip()
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning(
            "The pitch draft was unreadable (stop_reason=%s)", getattr(response, "stop_reason", None)
        )
        raise PitchWriterError("Claude's draft couldn't be read. Try again.") from error
    if not body or not subject:
        raise PitchWriterError("Claude's draft couldn't be read. Try again.")
    return subject[:MAX_SUBJECT_LENGTH], body[:MAX_BODY_LENGTH]
