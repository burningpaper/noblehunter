"""Import a validated profile config into the database.

Re-importing the same file (or an edited one) is safe:
- settings are updated, and genres, reference artists, anti-signals and tracks are
  replaced to match the file exactly;
- search terms are only ever added. Existing terms are never deleted by an import,
  because they may be approved suggestions or already tied to discovery history.

The caller owns the transaction: this flushes but never commits.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    AntiSignal,
    AntiSignalKind,
    Artist,
    Profile,
    ProfileGenre,
    ProfileTrack,
    ReferenceArtist,
    SearchTerm,
    SearchTermOrigin,
    SearchTermStatus,
)
from core.people import MAX_ARTIST_NAME_LENGTH
from core.profile_config import ProfileConfig
from core.text import normalize_text


@dataclass(frozen=True)
class ImportResult:
    profile_id: int
    created: bool
    terms_added: int


def import_profile(session: Session, artist_id: int, config: ProfileConfig) -> ImportResult:
    profile = session.scalar(
        select(Profile).where(Profile.artist_id == artist_id, Profile.name == config.name)
    )
    created = profile is None
    if created:
        profile = Profile(artist_id=artist_id, name=config.name)
        session.add(profile)

    profile.is_active = config.active
    profile.digest_target = config.digest_target
    _replace_lists(session, profile, config)
    terms_added = _add_missing_terms(session, profile, config.search_terms)

    session.flush()
    return ImportResult(profile_id=profile.id, created=created, terms_added=terms_added)


def artist_for_import(session: Session, name: str | None) -> int:
    """The artist named (created if it's new), or the only artist there is."""
    if name is not None:
        clean = " ".join(name.split())
        if not clean or len(clean) > MAX_ARTIST_NAME_LENGTH:
            raise LookupError(f"Give --artist a name of 1 to {MAX_ARTIST_NAME_LENGTH} characters.")
        artist = session.scalar(select(Artist).where(func.lower(Artist.name) == clean.lower()))
        if artist is None:
            artist = Artist(name=clean)
            session.add(artist)
            session.flush()
        return artist.id

    ids = list(session.scalars(select(Artist.id).order_by(Artist.id).limit(2)))
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise LookupError("There are no artists yet. Pass --artist NAME to create one.")
    raise LookupError("There's more than one artist. Say which with --artist NAME.")


def _replace_lists(session: Session, profile: Profile, config: ProfileConfig) -> None:
    # Delete the old rows first: re-adding an unchanged name in the same flush would
    # otherwise collide with the per-profile unique constraints.
    for collection in (profile.genres, profile.reference_artists, profile.anti_signals, profile.tracks):
        collection.clear()
    session.flush()

    profile.genres.extend(ProfileGenre(tag=tag, priority=index) for index, tag in enumerate(config.genres))
    profile.reference_artists.extend(ReferenceArtist(display_name=name) for name in config.reference_artists)
    profile.anti_signals.extend(
        [AntiSignal(kind=AntiSignalKind.ARTIST, value=value) for value in config.anti_signals.artists]
        + [AntiSignal(kind=AntiSignalKind.TERM, value=value) for value in config.anti_signals.terms]
    )
    profile.tracks.extend(
        ProfileTrack(title=track.title, spotify_url=track.spotify_url, description=track.description)
        for track in config.tracks
    )


def _add_missing_terms(session: Session, profile: Profile, terms: list[str]) -> int:
    existing = set(
        session.scalars(select(SearchTerm.normalized_term).where(SearchTerm.profile_id == profile.id))
    )
    added = 0
    for term in terms:
        key = normalize_text(term)
        if key in existing:
            continue
        profile.search_terms.append(
            SearchTerm(term=term, origin=SearchTermOrigin.MANUAL, status=SearchTermStatus.ACTIVE)
        )
        existing.add(key)
        added += 1
    return added
