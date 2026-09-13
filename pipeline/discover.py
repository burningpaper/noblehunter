"""Discover: turn a profile's search terms into candidate playlists, without repeating work.

For each active search term, both search engines are asked for Spotify playlists. What
happens next depends on what we already know:

- a playlist we've never seen becomes a `candidate`, named from its search result until the
  fetch stage replaces that with the real name;
- one that's excluded or already in the pipeline is left alone (no fetch is spent on it);
- one that's due for a re-check (e.g. no contact found 90+ days ago) goes back into the queue.

Either way, every hit is recorded in `playlist_sources` against the term and run, because
the weekly report needs to know which terms keep finding good playlists, including ones we
already knew about. Re-running within the same run records nothing twice.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.exclusion import playlist_ids_to_skip
from core.models import Playlist, PlaylistSource, PlaylistStatus, Run, SearchTerm, SearchTermStatus
from core.profiles import get_profile
from pipeline.runs import record_stage
from pipeline.search import Candidate, SearchProvider, discover

STAGE = "discover"
MAX_PLAYLIST_NAME_LENGTH = 300
UNTITLED = "Untitled playlist"
SPOTIFY_SUFFIX = re.compile(r"\s*\|\s*Spotify\b.*$", re.IGNORECASE)
PLAYLIST_BY_SUFFIX = re.compile(r"\s+-\s+playlist by\b.*$", re.IGNORECASE)


@dataclass(frozen=True)
class TermOutcome:
    term_id: int
    term: str
    candidates: int  # unique playlists the search engines returned for this term
    new: int  # never seen before, now candidates
    requeued: int  # known, due for a re-check, back in the queue
    skipped: int  # excluded or already in the pipeline
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class DiscoverySummary:
    profile_id: int
    run_id: int
    terms: tuple[TermOutcome, ...]

    @property
    def queued(self) -> int:
        return sum(outcome.new + outcome.requeued for outcome in self.terms)


def discover_for_profile(
    session: Session, profile_id: int, providers: Sequence[SearchProvider], *, run: Run, today: date
) -> DiscoverySummary:
    profile = get_profile(session, profile_id)
    active_terms = sorted(
        (term for term in profile.search_terms if term.status == SearchTermStatus.ACTIVE),
        key=lambda term: term.term.lower(),
    )
    outcomes = tuple(_discover_term(session, term, providers, run, today) for term in active_terms)

    record_stage(
        session,
        run,
        STAGE,
        count_in=sum(outcome.candidates for outcome in outcomes),
        count_out=sum(outcome.new + outcome.requeued for outcome in outcomes),
        profile_id=profile.id,
    )
    return DiscoverySummary(profile_id=profile.id, run_id=run.id, terms=outcomes)


def placeholder_name(title: str) -> str:
    """A search result title without the engine's "- playlist by X | Spotify" decoration."""
    name = PLAYLIST_BY_SUFFIX.sub("", SPOTIFY_SUFFIX.sub("", title.strip())).strip()
    return (name or UNTITLED)[:MAX_PLAYLIST_NAME_LENGTH]


def _discover_term(
    session: Session, term: SearchTerm, providers: Sequence[SearchProvider], run: Run, today: date
) -> TermOutcome:
    result = discover(term.term, providers)
    skip = playlist_ids_to_skip(session, [candidate.spotify_id for candidate in result.candidates], today)
    new = requeued = skipped = 0

    for candidate in result.candidates:
        playlist = session.get(Playlist, candidate.spotify_id)
        if playlist is None:
            session.add(
                Playlist(
                    spotify_id=candidate.spotify_id,
                    name=placeholder_name(candidate.hits[0].title),
                    status=PlaylistStatus.CANDIDATE,
                )
            )
            new += 1
        elif candidate.spotify_id in skip:
            skipped += 1
        else:
            playlist.status = PlaylistStatus.CANDIDATE
            playlist.rejection_reason = None
            requeued += 1
        session.flush()  # the playlist row must exist before its sources reference it
        _record_sources(session, candidate, term, run)

    return TermOutcome(
        term_id=term.id,
        term=term.term,
        candidates=len(result.candidates),
        new=new,
        requeued=requeued,
        skipped=skipped,
        warnings=result.warnings,
        errors=result.errors,
    )


def _record_sources(session: Session, candidate: Candidate, term: SearchTerm, run: Run) -> None:
    rows = [
        {
            "playlist_id": candidate.spotify_id,
            "run_id": run.id,
            "provider": hit.provider,
            "search_term_id": term.id,
            "rank": hit.rank,
        }
        for hit in candidate.hits
    ]
    session.execute(insert(PlaylistSource).values(rows).on_conflict_do_nothing())
