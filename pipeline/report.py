"""The leads report: qualified playlists worth a curator hunt, best fit first.

This is the first thing the pipeline produces that a person reads, so it answers the
questions Jarred would ask before opening a tab: what is it, who runs it, how big is it,
is it still being updated, and which of his reference artists are already on it. Curators
who are excluded, or who appeared in a digest within the last 90 days, are left out.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.exclusion import curator_is_eligible
from core.models import Playlist, PlaylistProfileFit, PlaylistStatus, Profile

PLAYLIST_URL = "https://open.spotify.com/playlist/{}"


@dataclass(frozen=True)
class QualifiedPlaylist:
    spotify_id: str
    name: str
    url: str
    profile_name: str
    curator_name: str | None
    followers: int | None
    size_band: str | None
    last_add_at: datetime | None
    fit_score: float
    reference_artists_present: tuple[str, ...]
    description: str | None


def qualified_playlists(
    session: Session, *, today: date, profile_id: int | None = None, limit: int | None = None
) -> list[QualifiedPlaylist]:
    query = (
        select(Playlist, PlaylistProfileFit, Profile)
        .join(PlaylistProfileFit, PlaylistProfileFit.playlist_id == Playlist.spotify_id)
        .join(Profile, Profile.id == PlaylistProfileFit.profile_id)
        .where(
            Playlist.status == PlaylistStatus.QUALIFIED,
            PlaylistProfileFit.qualified.is_(True),
            Profile.is_active.is_(True),
        )
        .order_by(
            PlaylistProfileFit.fit_score.desc(), Playlist.last_add_at.desc().nulls_last(), Playlist.spotify_id
        )
    )
    if profile_id is not None:
        query = query.where(Profile.id == profile_id)

    items: list[QualifiedPlaylist] = []
    for playlist, fit, profile in session.execute(query):
        if playlist.curator_id is not None and not curator_is_eligible(session, playlist.curator_id, today):
            continue
        items.append(_lead(playlist, fit, profile))
        if limit is not None and len(items) >= limit:
            break
    return items


def format_report(items: list[QualifiedPlaylist], *, today: date) -> str:
    if not items:
        return "No qualified playlists yet. Run `python -m pipeline.cli run` to look for some."

    lines = [f"{len(items)} qualified {'playlist' if len(items) == 1 else 'playlists'}"]
    for position, item in enumerate(items, start=1):
        lines.append("")
        lines.append(f"{position}. {item.name}  [{item.profile_name}]")
        lines.append(f"   {item.url}")
        lines.append(
            f"   curator: {item.curator_name or 'unknown'} · {_size(item)} · {_last_add(item, today)}"
        )
        artists = ", ".join(item.reference_artists_present) or "none"
        lines.append(f"   reference artists: {artists}")
    return "\n".join(lines)


def _lead(playlist: Playlist, fit: PlaylistProfileFit, profile: Profile) -> QualifiedPlaylist:
    curator_name = playlist.curator.display_name if playlist.curator else playlist.owner_name
    return QualifiedPlaylist(
        spotify_id=playlist.spotify_id,
        name=playlist.name,
        url=PLAYLIST_URL.format(playlist.spotify_id),
        profile_name=profile.name,
        curator_name=curator_name,
        followers=playlist.followers,
        size_band=playlist.size_band,
        last_add_at=playlist.last_add_at,
        fit_score=fit.fit_score,
        reference_artists_present=tuple(sorted(fit.reference_artists_present, key=str.casefold)),
        description=playlist.description,
    )


def _size(item: QualifiedPlaylist) -> str:
    if item.followers is None:
        return "followers unknown"
    return f"{item.followers:,} followers ({item.size_band})"


def _last_add(item: QualifiedPlaylist, today: date) -> str:
    if item.last_add_at is None:
        return "last add unknown"
    days = (today - item.last_add_at.astimezone(UTC).date()).days
    if days <= 0:
        return "last add today"
    return f"last add {days} {'day' if days == 1 else 'days'} ago"
