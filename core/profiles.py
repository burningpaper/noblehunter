"""Profiles: creating them, renaming them, and deciding when one is ready to go live.

A profile only joins the nightly run once it has enough to search with and judge against:
a genre, a few reference artists, a track to pitch and a handful of active search terms.
The rules live here, not in the web routes, so every way of changing a profile obeys them.
Validation reports every problem at once, in words Jarred can act on.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, contains_eager, selectinload

from core.access import Viewer, is_storable_id, visible_to
from core.models import Artist, Profile, SearchTermStatus
from core.profile_rules import (
    DEFAULT_DIGEST_TARGET,
    MAX_DIGEST_TARGET,
    MAX_MIN_FOLLOWERS,
    MAX_NAME_LENGTH,
    MAX_OPEN_CONVERSATIONS,
    MAX_QUIET_AFTER_DAYS,
    MIN_ACTIVE_SEARCH_TERMS,
    MIN_DIGEST_TARGET,
    MIN_GENRES,
    MIN_OPEN_CONVERSATIONS,
    MIN_QUIET_AFTER_DAYS,
    MIN_REFERENCE_ARTISTS,
    MIN_TRACKS,
)


class ProfileValidationError(ValueError):
    """One message per field, e.g. {"name": "Give the profile a name"}."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


@dataclass(frozen=True)
class ValidatedSettings:
    """A profile's settings, checked. `None` means "leave what's there"."""

    name: str
    digest_target: int
    min_followers: int | None = None
    open_conversation_limit: int | None = None
    quiet_after_days: int | None = None


@dataclass(frozen=True)
class ProfileSummary:
    id: int
    artist_id: int
    artist_name: str
    name: str
    is_active: bool
    digest_target: int
    genre_count: int
    reference_artist_count: int
    track_count: int
    active_term_count: int
    problems: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.problems


def get_profile(session: Session, profile_id: int) -> Profile:
    if not is_storable_id(profile_id):
        raise LookupError(f"Profile {profile_id} not found")
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise LookupError(f"Profile {profile_id} not found")
    return profile


def create_profile(
    session: Session, artist_id: int, name: str, digest_target: int | str = DEFAULT_DIGEST_TARGET
) -> Profile:
    if not is_storable_id(artist_id) or session.get(Artist, artist_id) is None:
        raise ProfileValidationError({"artist_id": "Choose which artist this profile is for"})
    settings = _validated_settings(session, artist_id, name, digest_target, profile_id=None)
    profile = Profile(artist_id=artist_id, name=settings.name, digest_target=settings.digest_target)
    session.add(profile)
    session.flush()
    return profile


def update_profile_settings(
    session: Session,
    profile_id: int,
    name: str,
    digest_target: int | str,
    min_followers: int | str | None = None,
    open_conversation_limit: int | str | None = None,
    quiet_after_days: int | str | None = None,
) -> Profile:
    """Rename and retune a profile. A setting left out keeps its current value."""
    profile = get_profile(session, profile_id)
    settings = _validated_settings(
        session,
        profile.artist_id,
        name,
        digest_target,
        profile_id,
        min_followers,
        open_conversation_limit,
        quiet_after_days,
    )
    profile.name, profile.digest_target = settings.name, settings.digest_target
    for field in ("min_followers", "open_conversation_limit", "quiet_after_days"):
        value = getattr(settings, field)
        if value is not None:
            setattr(profile, field, value)
    session.flush()
    return profile


def artist_choices(session: Session, viewer: Viewer) -> list[tuple[int, str]]:
    """The artists this viewer may create profiles under, as (id, name)."""
    rows = session.execute(
        select(Artist.id, Artist.name)
        .where(visible_to(viewer, Artist.id))
        .order_by(func.lower(Artist.name), Artist.id)
    )
    return [(artist_id, name) for artist_id, name in rows]


def set_profile_active(session: Session, profile_id: int, active: bool) -> Profile:
    profile = get_profile(session, profile_id)
    if active:
        problems = activation_problems(profile)
        if problems:
            raise ProfileValidationError({"active": "; ".join(problems)})
    profile.is_active = active
    session.flush()
    return profile


def activation_problems(profile: Profile) -> list[str]:
    return _problems(*_counts(profile))


def list_profiles(session: Session, viewer: Viewer) -> list[ProfileSummary]:
    profiles = session.scalars(
        select(Profile)
        .join(Artist, Artist.id == Profile.artist_id)
        .where(visible_to(viewer, Profile.artist_id))
        .options(
            contains_eager(Profile.artist),
            selectinload(Profile.genres),
            selectinload(Profile.reference_artists),
            selectinload(Profile.tracks),
            selectinload(Profile.search_terms),
        )
        .order_by(func.lower(Artist.name), Artist.id, func.lower(Profile.name), Profile.id)
    )
    return [_summarise(profile) for profile in profiles]


def _summarise(profile: Profile) -> ProfileSummary:
    genres, artists, tracks, terms = _counts(profile)
    return ProfileSummary(
        id=profile.id,
        artist_id=profile.artist_id,
        artist_name=profile.artist.name,
        name=profile.name,
        is_active=profile.is_active,
        digest_target=profile.digest_target,
        genre_count=genres,
        reference_artist_count=artists,
        track_count=tracks,
        active_term_count=terms,
        problems=tuple(_problems(genres, artists, tracks, terms)),
    )


def _counts(profile: Profile) -> tuple[int, int, int, int]:
    active_terms = sum(1 for term in profile.search_terms if term.status == SearchTermStatus.ACTIVE)
    return len(profile.genres), len(profile.reference_artists), len(profile.tracks), active_terms


def _problems(genres: int, artists: int, tracks: int, active_terms: int) -> list[str]:
    problems = []
    if genres < MIN_GENRES:
        problems.append("Add at least one genre")
    if artists < MIN_REFERENCE_ARTISTS:
        problems.append(f"Add at least {MIN_REFERENCE_ARTISTS} reference artists ({artists} so far)")
    if tracks < MIN_TRACKS:
        problems.append("Add at least one track to pitch")
    if active_terms < MIN_ACTIVE_SEARCH_TERMS:
        problems.append(f"Add at least {MIN_ACTIVE_SEARCH_TERMS} active search terms ({active_terms} so far)")
    return problems


def _validated_settings(
    session: Session,
    artist_id: int,
    name: str,
    digest_target: int | str,
    profile_id: int | None,
    min_followers: int | str | None = None,
    open_conversation_limit: int | str | None = None,
    quiet_after_days: int | str | None = None,
) -> ValidatedSettings:
    errors: dict[str, str] = {}
    clean_name = " ".join(str(name).split())
    if not clean_name:
        errors["name"] = "Give the profile a name"
    elif len(clean_name) > MAX_NAME_LENGTH:
        errors["name"] = f"Keep the name to {MAX_NAME_LENGTH} characters or fewer"
    elif _name_taken(session, artist_id, clean_name, profile_id):
        errors["name"] = f"A profile called “{clean_name}” already exists for this artist"

    target = _parse_digest_target(digest_target)
    if target is None:
        errors["digest_target"] = f"Choose a whole number between {MIN_DIGEST_TARGET} and {MAX_DIGEST_TARGET}"

    floor = _optional(
        min_followers,
        0,
        MAX_MIN_FOLLOWERS,
        errors,
        "min_followers",
        f"Choose a whole number of followers from 0 to {MAX_MIN_FOLLOWERS:,}",
    )
    ceiling = _optional(
        open_conversation_limit,
        MIN_OPEN_CONVERSATIONS,
        MAX_OPEN_CONVERSATIONS,
        errors,
        "open_conversation_limit",
        f"Choose a whole number between {MIN_OPEN_CONVERSATIONS} and {MAX_OPEN_CONVERSATIONS}",
    )
    quiet = _optional(
        quiet_after_days,
        MIN_QUIET_AFTER_DAYS,
        MAX_QUIET_AFTER_DAYS,
        errors,
        "quiet_after_days",
        f"Choose a whole number of days between {MIN_QUIET_AFTER_DAYS} and {MAX_QUIET_AFTER_DAYS}",
    )

    if errors:
        raise ProfileValidationError(errors)
    return ValidatedSettings(clean_name, target, floor, ceiling, quiet)


def _optional(
    value: int | str | None, lowest: int, highest: int, errors: dict[str, str], field: str, message: str
) -> int | None:
    """A setting the caller may leave out entirely; `None` in, `None` out, no error."""
    if value is None:
        return None
    text = str(value).strip()
    number = int(text) if text.isdigit() else None
    if number is None or not lowest <= number <= highest:
        errors[field] = message
        return None
    return number


def _parse_digest_target(value: int | str) -> int | None:
    text = str(value).strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if MIN_DIGEST_TARGET <= number <= MAX_DIGEST_TARGET else None


def _name_taken(session: Session, artist_id: int, name: str, profile_id: int | None) -> bool:
    query = select(Profile.id).where(Profile.artist_id == artist_id, func.lower(Profile.name) == name.lower())
    if profile_id is not None:
        query = query.where(Profile.id != profile_id)
    return session.scalar(query.limit(1)) is not None
