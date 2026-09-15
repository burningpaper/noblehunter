"""Profiles: creating them, renaming them, and deciding when one is ready to go live.

A profile only joins the nightly run once it has enough to search with and judge against:
a genre, a few reference artists, a track to pitch and a handful of active search terms.
The rules live here, not in the web routes, so every way of changing a profile obeys them.
Validation reports every problem at once, in words Jarred can act on.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, contains_eager, selectinload

from core.access import MAX_POSTGRES_INT, Viewer, visible_to
from core.models import Artist, Profile, SearchTermStatus
from core.profile_rules import (
    DEFAULT_DIGEST_TARGET,
    MAX_DIGEST_TARGET,
    MAX_MIN_FOLLOWERS,
    MAX_NAME_LENGTH,
    MIN_ACTIVE_SEARCH_TERMS,
    MIN_DIGEST_TARGET,
    MIN_GENRES,
    MIN_REFERENCE_ARTISTS,
    MIN_TRACKS,
)


class ProfileValidationError(ValueError):
    """One message per field, e.g. {"name": "Give the profile a name"}."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


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
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise LookupError(f"Profile {profile_id} not found")
    return profile


def create_profile(
    session: Session, artist_id: int, name: str, digest_target: int | str = DEFAULT_DIGEST_TARGET
) -> Profile:
    if not 0 < artist_id <= MAX_POSTGRES_INT or session.get(Artist, artist_id) is None:
        raise ProfileValidationError({"artist_id": "Choose which artist this profile is for"})
    clean_name, target, _ = _validated_settings(session, artist_id, name, digest_target, profile_id=None)
    profile = Profile(artist_id=artist_id, name=clean_name, digest_target=target)
    session.add(profile)
    session.flush()
    return profile


def update_profile_settings(
    session: Session,
    profile_id: int,
    name: str,
    digest_target: int | str,
    min_followers: int | str | None = None,
) -> Profile:
    """Rename and retune a profile. Leaving `min_followers` out keeps the current floor."""
    profile = get_profile(session, profile_id)
    clean_name, target, floor = _validated_settings(
        session, profile.artist_id, name, digest_target, profile_id, min_followers
    )
    profile.name, profile.digest_target = clean_name, target
    if floor is not None:
        profile.min_followers = floor
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
) -> tuple[str, int, int | None]:
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

    floor = None
    if min_followers is not None:
        floor = _parse_min_followers(min_followers)
        if floor is None:
            errors["min_followers"] = f"Choose a whole number of followers from 0 to {MAX_MIN_FOLLOWERS:,}"

    if errors:
        raise ProfileValidationError(errors)
    return clean_name, target, floor


def _parse_digest_target(value: int | str) -> int | None:
    text = str(value).strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if MIN_DIGEST_TARGET <= number <= MAX_DIGEST_TARGET else None


def _parse_min_followers(value: int | str) -> int | None:
    text = str(value).strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if number <= MAX_MIN_FOLLOWERS else None


def _name_taken(session: Session, artist_id: int, name: str, profile_id: int | None) -> bool:
    query = select(Profile.id).where(Profile.artist_id == artist_id, func.lower(Profile.name) == name.lower())
    if profile_id is not None:
        query = query.where(Profile.id != profile_id)
    return session.scalar(query.limit(1)) is not None
