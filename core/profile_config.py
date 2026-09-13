"""Profile config files: what an artist profile looks like before it reaches the database.

Profiles are normally edited in the web app, but a YAML file is how the first profile gets
in (and how a profile can be backed up or recreated). Everything is validated here, and
every problem is reported at once, so a typo'd field name or a duplicated artist is caught
before it quietly skews a night's scoring.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.profile_rules import MAX_DIGEST_TARGET, MIN_ACTIVE_SEARCH_TERMS, MIN_REFERENCE_ARTISTS
from core.spotify_urls import canonical_track_url
from core.text import normalize_text


class ProfileConfigError(ValueError):
    """The profile config file is missing, unreadable or invalid."""


__all__ = ["ProfileConfig", "ProfileConfigError", "load_profile_config", "normalize_text"]


def _clean_entries(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(value.split())
        if not text:
            raise ValueError("entries can't be blank")
        key = normalize_text(text)
        if key in seen:
            raise ValueError(f"Duplicate entry {key!r}")
        seen.add(key)
        cleaned.append(text)
    return cleaned


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Track(_Strict):
    title: str = Field(min_length=1, max_length=200)
    spotify_url: str
    description: str = Field(default="", max_length=300)

    @field_validator("spotify_url")
    @classmethod
    def check_track_url(cls, value: str) -> str:
        canonical = canonical_track_url(value)
        if canonical is None:
            raise ValueError("must be an open.spotify.com/track/... link")
        return canonical


class AntiSignals(_Strict):
    artists: list[str] = []
    terms: list[str] = []

    @field_validator("artists", "terms")
    @classmethod
    def clean(cls, values: list[str]) -> list[str]:
        return _clean_entries(values)


class ProfileConfig(_Strict):
    name: str = Field(min_length=1, max_length=80)
    active: bool = False
    digest_target: int = Field(default=20, ge=1, le=MAX_DIGEST_TARGET)
    genres: list[str] = Field(min_length=1)
    reference_artists: list[str] = Field(min_length=MIN_REFERENCE_ARTISTS)
    anti_signals: AntiSignals = AntiSignals()
    tracks: list[Track] = Field(min_length=1)
    search_terms: list[str] = []

    @field_validator("genres", "reference_artists", "search_terms")
    @classmethod
    def clean(cls, values: list[str]) -> list[str]:
        return _clean_entries(values)

    @model_validator(mode="after")
    def active_profiles_need_search_terms(self) -> "ProfileConfig":
        if self.active and len(self.search_terms) < MIN_ACTIVE_SEARCH_TERMS:
            raise ValueError(f"An active profile needs at least {MIN_ACTIVE_SEARCH_TERMS} search terms")
        return self


def load_profile_config(path: Path | str) -> ProfileConfig:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ProfileConfigError(f"Profile config not found: {path}") from None

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark else "line unknown"
        raise ProfileConfigError(f"Profile config {path} is not valid YAML ({where}).") from None

    if not isinstance(data, dict):
        raise ProfileConfigError(f"Profile config {path} must be a mapping of fields.")

    try:
        return ProfileConfig.model_validate(data)
    except ValidationError as error:
        raise ProfileConfigError(_describe(path, error)) from None


def _describe(path: Path, error: ValidationError) -> str:
    lines = [f"Profile config {path} has {error.error_count()} problem(s):"]
    for problem in error.errors():
        field = ".".join(str(part) for part in problem["loc"]) or "profile"
        message = problem["msg"].removeprefix("Value error, ")
        lines.append(f"  - {field}: {message}")
    return "\n".join(lines)
