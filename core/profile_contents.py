"""What goes inside a profile: genres, reference artists, anti-signals, tracks and search terms.

Every change is validated with messages Jarred can act on, and duplicates are caught the way
a person would see them ("µ-Ziq" and "μ-ziq", "IDM" and " idm ", are the same thing). Items
are always looked up *through* their profile, so an id from another profile is simply not
found. And because an active profile must stay runnable, anything that removes or pauses an
essential ingredient re-checks readiness and pauses the profile if it no longer qualifies;
those functions return True when that happened so the page can say so.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    AntiSignal,
    AntiSignalKind,
    PlaylistSource,
    Profile,
    ProfileGenre,
    ProfileTrack,
    ReferenceArtist,
    SearchTerm,
    SearchTermOrigin,
    SearchTermStatus,
)
from core.profile_rules import (
    MAX_ANTI_SIGNAL_LENGTH,
    MAX_ARTIST_LENGTH,
    MAX_GENRE_LENGTH,
    MAX_TERM_LENGTH,
    MAX_TRACK_DESCRIPTION_LENGTH,
    MAX_TRACK_TITLE_LENGTH,
    MIN_TERM_LENGTH,
    SEARCH_REQUESTS_PER_TERM,
)
from core.profiles import ProfileValidationError, activation_problems, get_profile
from core.spotify_urls import canonical_track_url
from core.text import normalize_text

CHOOSABLE_TERM_STATUSES = frozenset({SearchTermStatus.ACTIVE, SearchTermStatus.PAUSED})
MOVE_DIRECTIONS = frozenset({"up", "down"})


# --- Genres -----------------------------------------------------------------------------


def add_genre(session: Session, profile_id: int, tag: str) -> ProfileGenre:
    profile = get_profile(session, profile_id)
    errors: dict[str, str] = {}
    clean = _required_text(errors, "tag", tag, MAX_GENRE_LENGTH, "a genre")
    if "tag" not in errors and _already_listed(profile.genres, "tag", clean):
        errors["tag"] = f"“{clean}” is already a genre"
    _raise_if_any(errors)

    genre = ProfileGenre(tag=clean, priority=max((g.priority for g in profile.genres), default=-1) + 1)
    profile.genres.append(genre)
    session.flush()
    return genre


def move_genre(session: Session, profile_id: int, genre_id: int, direction: str) -> None:
    if direction not in MOVE_DIRECTIONS:
        raise ValueError(f"Unknown direction {direction!r}; use 'up' or 'down'")
    profile = get_profile(session, profile_id)
    ordered = _genres_in_order(profile)
    index = ordered.index(_owned(ordered, genre_id, "Genre"))
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(ordered):
        ordered[index], ordered[target] = ordered[target], ordered[index]
    _renumber(ordered)
    session.flush()


def remove_genre(session: Session, profile_id: int, genre_id: int) -> bool:
    profile = get_profile(session, profile_id)
    profile.genres.remove(_owned(profile.genres, genre_id, "Genre"))
    session.flush()
    _renumber(_genres_in_order(profile))
    return _keep_ready(session, profile)


# --- Reference artists ------------------------------------------------------------------


def add_reference_artist(session: Session, profile_id: int, name: str) -> ReferenceArtist:
    profile = get_profile(session, profile_id)
    errors: dict[str, str] = {}
    clean = _required_text(errors, "name", name, MAX_ARTIST_LENGTH, "an artist name")
    if "name" not in errors and _already_listed(profile.reference_artists, "display_name", clean):
        errors["name"] = f"“{clean}” is already a reference artist"
    _raise_if_any(errors)

    artist = ReferenceArtist(display_name=clean)
    profile.reference_artists.append(artist)
    session.flush()
    return artist


def remove_reference_artist(session: Session, profile_id: int, artist_id: int) -> bool:
    profile = get_profile(session, profile_id)
    profile.reference_artists.remove(_owned(profile.reference_artists, artist_id, "Reference artist"))
    return _keep_ready(session, profile)


# --- Anti-signals -----------------------------------------------------------------------


def add_anti_signal(session: Session, profile_id: int, kind: str, value: str) -> AntiSignal:
    profile = get_profile(session, profile_id)
    errors: dict[str, str] = {}
    if kind not in {member.value for member in AntiSignalKind}:
        errors["kind"] = "Choose whether this is an artist or a term"
    clean = _required_text(errors, "value", value, MAX_ANTI_SIGNAL_LENGTH, "an artist or term")
    same_kind = [signal for signal in profile.anti_signals if signal.kind == kind]
    if "value" not in errors and "kind" not in errors and _already_listed(same_kind, "value", clean):
        errors["value"] = f"“{clean}” is already an anti-signal {kind}"
    _raise_if_any(errors)

    signal = AntiSignal(kind=kind, value=clean)
    profile.anti_signals.append(signal)
    session.flush()
    return signal


def remove_anti_signal(session: Session, profile_id: int, signal_id: int) -> bool:
    profile = get_profile(session, profile_id)
    profile.anti_signals.remove(_owned(profile.anti_signals, signal_id, "Anti-signal"))
    return _keep_ready(session, profile)


# --- Tracks -----------------------------------------------------------------------------


def add_track(
    session: Session, profile_id: int, title: str, spotify_url: str, description: str = ""
) -> ProfileTrack:
    profile = get_profile(session, profile_id)
    errors: dict[str, str] = {}
    clean_title = _required_text(errors, "title", title, MAX_TRACK_TITLE_LENGTH, "the track title")
    url = canonical_track_url(spotify_url or "")
    if url is None:
        errors["spotify_url"] = "Paste the track's open.spotify.com/track/… link"
    elif any(track.spotify_url == url for track in profile.tracks):
        errors["spotify_url"] = "This track is already on the profile"
    clean_description = " ".join(str(description or "").split())
    if len(clean_description) > MAX_TRACK_DESCRIPTION_LENGTH:
        errors["description"] = f"Keep the description to {MAX_TRACK_DESCRIPTION_LENGTH} characters or fewer"
    _raise_if_any(errors)

    track = ProfileTrack(title=clean_title, spotify_url=url, description=clean_description)
    profile.tracks.append(track)
    session.flush()
    return track


def remove_track(session: Session, profile_id: int, track_id: int) -> bool:
    profile = get_profile(session, profile_id)
    profile.tracks.remove(_owned(profile.tracks, track_id, "Track"))
    return _keep_ready(session, profile)


# --- Search terms -----------------------------------------------------------------------


def add_search_term(
    session: Session,
    profile_id: int,
    term: str,
    *,
    origin: str = SearchTermOrigin.MANUAL,
    rationale: str | None = None,
) -> SearchTerm:
    """Add an active term. Claude's suggestions arrive with origin `suggested` and the reason given."""
    profile = get_profile(session, profile_id)
    clean = " ".join(str(term).split())
    errors: dict[str, str] = {}
    if len(clean) < MIN_TERM_LENGTH:
        errors["term"] = f"Search terms need at least {MIN_TERM_LENGTH} characters"
    elif len(clean) > MAX_TERM_LENGTH:
        errors["term"] = f"Keep search terms to {MAX_TERM_LENGTH} characters or fewer"
    elif _already_listed(profile.search_terms, "term", clean):
        errors["term"] = f"“{clean}” is already a search term"
    _raise_if_any(errors)

    search_term = SearchTerm(term=clean, origin=origin, status=SearchTermStatus.ACTIVE, rationale=rationale)
    profile.search_terms.append(search_term)
    session.flush()
    return search_term


def set_search_term_status(session: Session, profile_id: int, term_id: int, status: str) -> bool:
    if status not in CHOOSABLE_TERM_STATUSES:
        raise ProfileValidationError({"status": "Choose active or paused"})
    profile = get_profile(session, profile_id)
    _owned(profile.search_terms, term_id, "Search term").status = status
    return _keep_ready(session, profile)


def remove_search_term(session: Session, profile_id: int, term_id: int) -> bool:
    profile = get_profile(session, profile_id)
    term = _owned(profile.search_terms, term_id, "Search term")
    found_playlists = session.scalar(
        select(func.count()).select_from(PlaylistSource).where(PlaylistSource.search_term_id == term.id)
    )
    if found_playlists:
        raise ProfileValidationError(
            {"term": "This term has already found playlists, so it's kept for history. Pause it instead."}
        )
    profile.search_terms.remove(term)
    return _keep_ready(session, profile)


def search_requests_per_night(profile: Profile) -> int:
    active = sum(1 for term in profile.search_terms if term.status == SearchTermStatus.ACTIVE)
    return active * SEARCH_REQUESTS_PER_TERM


# --- Helpers ----------------------------------------------------------------------------


def _required_text(errors: dict[str, str], field: str, value: str, max_length: int, label: str) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        errors[field] = f"Enter {label}"
    elif len(clean) > max_length:
        errors[field] = f"Keep {label} to {max_length} characters or fewer"
    return clean


def _already_listed(items, attribute: str, value: str) -> bool:
    wanted = normalize_text(value)
    return any(normalize_text(getattr(item, attribute)) == wanted for item in items)


def _raise_if_any(errors: dict[str, str]) -> None:
    if errors:
        raise ProfileValidationError(errors)


def _owned(items, item_id: int, what: str):
    for item in items:
        if item.id == item_id:
            return item
    raise LookupError(f"{what} {item_id} not found on this profile")


def _genres_in_order(profile: Profile) -> list[ProfileGenre]:
    return sorted(profile.genres, key=lambda genre: genre.priority)


def _renumber(genres: list[ProfileGenre]) -> None:
    for position, genre in enumerate(genres):
        genre.priority = position


def _keep_ready(session: Session, profile: Profile) -> bool:
    """Pause an active profile that no longer has what it needs. True if it was paused."""
    session.flush()
    if profile.is_active and activation_problems(profile):
        profile.is_active = False
        session.flush()
        return True
    return False
