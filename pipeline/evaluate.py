"""Evaluate: fetch each candidate from Spotify, store what it says, and record the verdict.

Candidates are taken oldest first, optionally capped, and judged against every *active*
profile at once, so one fetch serves them all. Each playlist is committed as soon as it's
done: a run that dies halfway keeps everything it already learned.

Failures are handled by what they mean, not by what went wrong technically:

- `not_found`: the playlist is gone. That's a dead playlist, re-checked after 90 days like
  any other.
- `timeout`, `http` or `parse`: our problem or Spotify's, not the playlist's. Mark it
  `fetch-failed` so the next run tries again.
- `blocked`: Spotify is pushing back. Stop the whole run immediately, leave the rest queued,
  and don't make it worse.

If no profile is active there's nothing to judge against, so nothing is fetched. Otherwise
every candidate would be wrongly rejected as no fit.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from core.models import (
    Curator,
    Playlist,
    PlaylistProfileFit,
    PlaylistStatus,
    Profile,
    Reachability,
    RejectionReason,
    Run,
)
from pipeline.qualify import SPOTIFY_OWNERS, ProfileRules, Verdict, judge
from pipeline.runs import record_stage
from pipeline.spotify import PlaylistData, SpotifyFetchError

MAX_PLAYLIST_NAME_LENGTH = 300
MAX_CURATOR_NAME_LENGTH = 200
NO_ACTIVE_PROFILES = "No active profiles: activate one before evaluating candidates."


class PlaylistFetcher(Protocol):
    def fetch_playlist(self, spotify_id: str) -> PlaylistData: ...


@dataclass(frozen=True)
class EvaluationSummary:
    fetched: int
    failed: int
    qualified: int
    rejected: int
    blocked: bool
    errors: tuple[str, ...]


def evaluate_candidates(
    session: Session,
    fetcher: PlaylistFetcher,
    *,
    run: Run,
    today: date,
    now: datetime,
    limit: int | None = None,
) -> EvaluationSummary:
    rules = _active_profile_rules(session)
    if not rules:
        return EvaluationSummary(0, 0, 0, 0, blocked=False, errors=(NO_ACTIVE_PROFILES,))

    candidate_ids = _candidate_ids(session, limit)
    fetched = failed = qualified = rejected = 0
    blocked = False
    errors: list[str] = []

    for spotify_id in candidate_ids:
        playlist = session.get(Playlist, spotify_id)
        try:
            data = fetcher.fetch_playlist(spotify_id)
        except SpotifyFetchError as error:
            errors.append(f"{spotify_id}: {error}")
            if error.kind == "blocked":
                blocked = True
                break
            failed += 1
            _record_fetch_failure(playlist, error, now)
            session.commit()
            continue

        fetched += 1
        verdict = judge(data, rules, today)
        _store_playlist(session, playlist, data, verdict, now)
        _store_fits(session, spotify_id, verdict, rules, now)
        if verdict.status == PlaylistStatus.QUALIFIED:
            qualified += 1
        else:
            rejected += 1
        session.commit()

    record_stage(session, run, "fetch", count_in=len(candidate_ids), count_out=fetched)
    record_stage(session, run, "qualify", count_in=fetched, count_out=qualified)
    session.commit()
    return EvaluationSummary(fetched, failed, qualified, rejected, blocked, tuple(errors))


def _active_profile_rules(session: Session) -> list[ProfileRules]:
    profiles = session.scalars(
        select(Profile)
        .where(Profile.is_active.is_(True))
        .options(selectinload(Profile.reference_artists), selectinload(Profile.anti_signals))
        .order_by(Profile.id)
    )
    return [ProfileRules.from_profile(profile) for profile in profiles]


def _candidate_ids(session: Session, limit: int | None) -> list[str]:
    query = (
        select(Playlist.spotify_id)
        .where(Playlist.status == PlaylistStatus.CANDIDATE)
        .order_by(Playlist.first_seen_at, Playlist.spotify_id)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def _record_fetch_failure(playlist: Playlist, error: SpotifyFetchError, now: datetime) -> None:
    playlist.last_checked_at = now
    if error.kind == "not_found":
        playlist.status = PlaylistStatus.REJECTED
        playlist.rejection_reason = RejectionReason.NOT_ALIVE
        playlist.is_alive = False
    else:
        playlist.status = PlaylistStatus.FETCH_FAILED
        playlist.rejection_reason = None


def _store_playlist(
    session: Session, playlist: Playlist, data: PlaylistData, verdict: Verdict, now: datetime
) -> None:
    if data.name:
        playlist.name = data.name[:MAX_PLAYLIST_NAME_LENGTH]
    playlist.description = data.description_text or None
    playlist.owner_spotify_id = data.owner_id
    playlist.owner_name = data.owner_name
    playlist.followers = data.followers
    playlist.track_count = data.total_tracks
    playlist.cover_image_url = data.cover_image_url
    playlist.last_add_at = verdict.liveness.last_add_at
    playlist.size_band = verdict.size_band
    playlist.is_alive = verdict.liveness.alive
    playlist.is_real = verdict.reality.real
    playlist.status = verdict.status
    playlist.rejection_reason = verdict.rejection_reason
    playlist.last_checked_at = now

    spotify_owned = data.owner_id in SPOTIFY_OWNERS
    playlist.reachability = Reachability.SPOTIFY if spotify_owned else Reachability.CURATOR
    if data.owner_id and not spotify_owned:
        playlist.curator = _curator_for(session, data)
    session.flush()


def _curator_for(session: Session, data: PlaylistData) -> Curator:
    curator = session.scalar(select(Curator).where(Curator.spotify_user_id == data.owner_id))
    display_name = (data.owner_name or data.owner_id)[:MAX_CURATOR_NAME_LENGTH]
    if curator is None:
        curator = Curator(spotify_user_id=data.owner_id, display_name=display_name)
        session.add(curator)
    elif data.owner_name:
        curator.display_name = display_name
    return curator


def _store_fits(
    session: Session, spotify_id: str, verdict: Verdict, rules: list[ProfileRules], now: datetime
) -> None:
    qualified_ids = set(verdict.qualified_profile_ids)
    for profile_rules in rules:
        fit = verdict.fits[profile_rules.profile_id]
        values = {
            "fit_score": fit.score,
            "reference_artists_present": list(fit.reference_artists_present),
            "genre_tags": [],
            "qualified": profile_rules.profile_id in qualified_ids,
            "scored_at": now,
        }
        statement = insert(PlaylistProfileFit).values(
            playlist_id=spotify_id, profile_id=profile_rules.profile_id, **values
        )
        session.execute(
            statement.on_conflict_do_update(index_elements=["playlist_id", "profile_id"], set_=values)
        )
