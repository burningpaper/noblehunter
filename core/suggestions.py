"""Ask Claude: suggestions for a profile's genres, reference artists, anti-signals and search terms.

Filling in a profile means knowing a scene well: which genre tags curators really use, which
artists mark a playlist as the right room, which words give away the wrong one. Instead of
switching to another app, Jarred asks Claude from inside the editor.

Claude sees what the profile already has and answers in a fixed JSON shape. Every suggestion
is then checked again here: blanks, repeats and anything already on the profile are dropped.
The ones Jarred ticks go in through the same validated functions as typed entries, so nothing
lands on a profile that he didn't choose and that a typed entry couldn't have been.

The call is deliberately small: one request, thinking off, low effort, a bounded answer. It
costs about a cent and comes back in seconds.
"""

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import anthropic
from sqlalchemy.orm import Session

from core import profile_contents as contents
from core.models import AntiSignalKind, Profile, SearchTermOrigin
from core.profiles import ProfileValidationError
from core.text import normalize_text

logger = logging.getLogger("noble_hunter.suggestions")

DEFAULT_MODEL = "claude-opus-5"
MAX_OUTPUT_TOKENS = 2_000
REQUEST_TIMEOUT_SECONDS = 60
MAX_PROMPT_LENGTH = 500
MAX_SUGGESTIONS = 12
MAX_REASON_LENGTH = 300

SECTIONS = {
    "genres": "Genres",
    "artists": "Reference artists",
    "anti_signals": "Anti-signals",
    "terms": "Search terms",
}
PLACEHOLDERS = {
    "genres": "e.g. Which genre tags do curators use for this kind of music?",
    "artists": "e.g. More artists whose fans would like this music",
    "anti_signals": "e.g. Artists or words that mean a playlist is the wrong vibe",
    "terms": "e.g. What search terms would find playlists for this music on Spotify?",
}
SECTION_GUIDANCE = {
    "genres": "Genres are the tags curators actually use in playlist titles and descriptions.",
    "artists": (
        "Reference artists are artists whose presence on a playlist shows it's the right place "
        "for this music."
    ),
    "anti_signals": (
        'Anti-signals are artists (kind "artist") or words (kind "term") whose presence shows a playlist '
        "is the wrong place for this music."
    ),
    "terms": (
        "Search terms are short phrases a web search would match against Spotify playlist pages, like words "
        "in a playlist's title or description. Don't add quotes or site: operators; the tool adds those."
    ),
}
SYSTEM_PROMPT = (
    "You help a musician find Spotify playlist curators to pitch their music to. They keep a profile for "
    "each style of their music: genres, reference artists, anti-signals and search terms. You suggest new "
    "entries for one part of a profile.\n\n"
    "Be specific to the music the profile describes: niche and accurate beats popular and vague. Only name "
    "artists you're confident exist, spelled as they appear on Spotify. Never repeat anything the profile "
    f"already has. Suggest up to {MAX_SUGGESTIONS} entries, each with a one-sentence reason "
    "that's quick to judge."
)
ANTI_SIGNAL_KINDS = frozenset(kind.value for kind in AntiSignalKind)


@dataclass(frozen=True)
class Suggestion:
    value: str
    reason: str = ""
    kind: str | None = None  # anti-signals only: "artist" or "term"


@dataclass(frozen=True)
class ProfileContext:
    name: str
    genres: tuple[str, ...]
    reference_artists: tuple[str, ...]
    anti_artists: tuple[str, ...]
    anti_terms: tuple[str, ...]
    search_terms: tuple[str, ...]


@dataclass(frozen=True)
class AddResult:
    added: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"Added {len(self.added)}: {', '.join(self.added)}.")
        if self.skipped:
            parts.append(f"Not added (already there or not valid): {', '.join(self.skipped)}.")
        return " ".join(parts)


class SuggestionError(RuntimeError):
    """Claude couldn't help this time. The message is safe to show Jarred."""


class Suggester(Protocol):
    def suggest(self, section: str, prompt: str, context: ProfileContext) -> list[Suggestion]: ...


def require_section(section: str) -> str:
    if section not in SECTIONS:
        raise ValueError(f"Unknown suggestion section {section!r}; expected one of: {', '.join(SECTIONS)}")
    return section


def validate_prompt(prompt: str) -> str:
    clean = str(prompt or "").strip()
    if not clean:
        raise ProfileValidationError({"prompt": "Ask Claude something first, e.g. “more artists like these”"})
    if len(clean) > MAX_PROMPT_LENGTH:
        raise ProfileValidationError(
            {"prompt": f"Keep the question to {MAX_PROMPT_LENGTH} characters or fewer"}
        )
    return clean


def profile_context(profile: Profile) -> ProfileContext:
    signals = profile.anti_signals
    return ProfileContext(
        name=profile.name,
        genres=tuple(genre.tag for genre in sorted(profile.genres, key=lambda genre: genre.priority)),
        reference_artists=tuple(artist.display_name for artist in profile.reference_artists),
        anti_artists=tuple(signal.value for signal in signals if signal.kind == AntiSignalKind.ARTIST),
        anti_terms=tuple(signal.value for signal in signals if signal.kind == AntiSignalKind.TERM),
        search_terms=tuple(term.term for term in profile.search_terms),
    )


def new_suggestions(profile: Profile, section: str, suggestions: Iterable[Suggestion]) -> list[Suggestion]:
    """Claude's answer minus blanks, repeats and anything the profile already has, capped."""
    require_section(section)
    seen = _existing_keys(profile, section)
    fresh: list[Suggestion] = []
    for suggestion in suggestions:
        tidy = _tidy(section, suggestion)
        if tidy is None:
            continue
        key = (tidy.kind, normalize_text(tidy.value))
        if key in seen:
            continue
        seen.add(key)
        fresh.append(tidy)
        if len(fresh) == MAX_SUGGESTIONS:
            break
    return fresh


def add_suggestions(
    session: Session, profile_id: int, section: str, chosen: Sequence[Suggestion]
) -> AddResult:
    """Add what Jarred ticked. Items that fail validation (usually: already there) are skipped, not fatal."""
    require_section(section)
    if not chosen:
        raise ProfileValidationError({"choice": "Tick at least one suggestion to add"})

    added: list[str] = []
    skipped: list[str] = []
    for suggestion in chosen:
        try:
            _add_one(session, profile_id, section, suggestion)
        except ProfileValidationError:
            skipped.append(suggestion.value)
        else:
            added.append(suggestion.value)
    return AddResult(tuple(added), tuple(skipped))


class ClaudeSuggester:
    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model

    @classmethod
    def from_api_key(cls, api_key: str) -> "ClaudeSuggester":
        return cls(anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1))

    def suggest(self, section: str, prompt: str, context: ProfileContext) -> list[Suggestion]:
        require_section(section)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                thinking={"type": "disabled"},
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": _answer_schema(section)},
                },
                messages=[{"role": "user", "content": _question(section, prompt, context)}],
            )
        except anthropic.APIError as error:
            logger.warning("Ask Claude failed for %s: %s", section, type(error).__name__)
            raise SuggestionError("Claude didn't answer. Try again in a moment.") from error
        return _parse_answer(response)


def _tidy(section: str, suggestion: Suggestion) -> Suggestion | None:
    value = " ".join(str(suggestion.value).split())
    if not value:
        return None
    kind = suggestion.kind if section == "anti_signals" else None
    if section == "anti_signals" and kind not in ANTI_SIGNAL_KINDS:
        return None
    return Suggestion(value, " ".join(str(suggestion.reason).split()), kind)


def _existing_keys(profile: Profile, section: str) -> set[tuple[str | None, str]]:
    if section == "genres":
        values = [(None, genre.tag) for genre in profile.genres]
    elif section == "artists":
        values = [(None, artist.display_name) for artist in profile.reference_artists]
    elif section == "anti_signals":
        values = [(signal.kind, signal.value) for signal in profile.anti_signals]
    else:
        values = [(None, term.term) for term in profile.search_terms]
    return {(kind, normalize_text(value)) for kind, value in values}


def _add_one(session: Session, profile_id: int, section: str, suggestion: Suggestion) -> None:
    if section == "genres":
        contents.add_genre(session, profile_id, suggestion.value)
    elif section == "artists":
        contents.add_reference_artist(session, profile_id, suggestion.value)
    elif section == "anti_signals":
        contents.add_anti_signal(session, profile_id, suggestion.kind or "", suggestion.value)
    else:
        rationale = " ".join(suggestion.reason.split())[:MAX_REASON_LENGTH] or None
        contents.add_search_term(
            session, profile_id, suggestion.value, origin=SearchTermOrigin.SUGGESTED, rationale=rationale
        )


def _answer_schema(section: str) -> dict:
    properties: dict[str, dict] = {"value": {"type": "string"}, "reason": {"type": "string"}}
    if section == "anti_signals":
        properties["kind"] = {"type": "string", "enum": sorted(ANTI_SIGNAL_KINDS)}
    item = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"suggestions": {"type": "array", "items": item}},
        "required": ["suggestions"],
        "additionalProperties": False,
    }


def _question(section: str, prompt: str, context: ProfileContext) -> str:
    return "\n".join(
        [
            f"Profile: {context.name}",
            _listing("Genres", context.genres),
            _listing("Reference artists", context.reference_artists),
            _listing("Anti-signal artists", context.anti_artists),
            _listing("Anti-signal terms", context.anti_terms),
            _listing("Search terms", context.search_terms),
            "",
            f"Suggest new {SECTIONS[section].lower()} for this profile. {SECTION_GUIDANCE[section]}",
            f"Request: {prompt}",
        ]
    )


def _listing(label: str, values: tuple[str, ...]) -> str:
    return f"{label}: {'; '.join(values) if values else '(none yet)'}"


def _parse_answer(response) -> list[Suggestion]:
    text = next((block.text for block in response.content if block.type == "text"), None)
    try:
        items = json.loads(text or "")["suggestions"]
        return [
            Suggestion(str(item["value"]), str(item.get("reason") or ""), item.get("kind")) for item in items
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning(
            "Ask Claude gave an unreadable answer (stop_reason=%s)", getattr(response, "stop_reason", None)
        )
        raise SuggestionError("Claude's answer couldn't be read. Try asking again.") from error
