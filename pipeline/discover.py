"""Discover: turn a profile's search terms, and the artists it sounds like, into candidates.

There are two pools, and they barely overlap.

**Search.** For each active search term, both search engines are asked for Spotify playlists.

**The artist graph.** Each reference artist's Spotify page carries a "Discovered on" section --
the playlists people found that artist through. The spike on 2026-09-20 measured why this is
here: of 96 pitchable playlists it named, 90 had never been seen by five days of web search,
because a playlist reaches a search index by being linked to from the web and most good ones
never are. It runs first, so a night that finds plenty there still runs every term.

What happens next is the same for both, and deliberately so -- `_admit` is the one funnel:

- a playlist we've never seen becomes a `candidate`, named from its search result until the
  fetch stage replaces that with the real name;
- one that's excluded or already in the pipeline is left alone (no fetch is spent on it);
- one that's due for a re-check (e.g. no contact found 90+ days ago) goes back into the queue.

Either way, every hit is recorded in `playlist_sources` against the term and run, because
the weekly report needs to know which terms keep finding good playlists, including ones we
already knew about. Re-running within the same run records nothing twice. A playlist from the
artist graph is recorded against no term at all, because there isn't one; see
`_record_discovered_on` for what the database does about that.

The artist graph is **off unless a client is handed in**. Every existing caller passes none and
behaves exactly as it did, and the unit tests run without Chromium.
"""

import re
from collections import Counter
from collections.abc import Container, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.exclusion import playlist_ids_to_skip
from core.models import (
    Playlist,
    PlaylistSource,
    PlaylistStatus,
    Profile,
    Run,
    SearchTerm,
    SearchTermStatus,
    SourceProvider,
)
from core.profiles import get_profile
from pipeline.artist_ids import resolve_missing_artist_ids
from pipeline.discovered_on import discovered_on_playlist_ids
from pipeline.runs import record_stage
from pipeline.search import Candidate, SearchProvider, discover

STAGE = "discover"
MAX_PLAYLIST_NAME_LENGTH = 300
UNTITLED = "Untitled playlist"
SPOTIFY_SUFFIX = re.compile(r"\s*\|\s*Spotify\b.*$", re.IGNORECASE)
PLAYLIST_BY_SUFFIX = re.compile(r"\s+-\s+playlist by\b.*$", re.IGNORECASE)

NEW, REQUEUED, SKIPPED = "new", "requeued", "skipped"


class ArtistGraph(Protocol):
    """What this step needs of `SpotifyWebClient`: a name to an id, and an id to an artist page.

    Narrow, and injected rather than built here. Playwright's sync API refuses to start a second
    browser while one is running, so the nightly run opens exactly one client and hands it down.
    A source that opened its own would fail at 02:00 rather than in a test, and would drag
    Chromium into every unit test that touches discovery.
    """

    def fetch_artist_search(self, name: str) -> object: ...

    def fetch_artist_overview(self, artist_id: str) -> object: ...


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
class DiscoveredOnOutcome:
    """What the artist graph contributed for one profile, in one run."""

    artists: int  # reference artists whose page was actually read
    playlists: int  # unique ids those pages named; repeats across artists collapse into one
    new: int
    requeued: int
    skipped: int
    unresolved: tuple[str, ...]  # reference artists with no Spotify id, named so they can be fixed
    errors: tuple[str, ...]  # one per artist page that failed, and that artist's cost alone


@dataclass(frozen=True)
class DiscoverySummary:
    profile_id: int
    run_id: int
    terms: tuple[TermOutcome, ...]
    discovered_on: DiscoveredOnOutcome | None = None  # None means the source was not switched on

    @property
    def queued(self) -> int:
        queued = sum(outcome.new + outcome.requeued for outcome in self.terms)
        if self.discovered_on is not None:
            queued += self.discovered_on.new + self.discovered_on.requeued
        return queued


def discover_for_profile(
    session: Session,
    profile_id: int,
    providers: Sequence[SearchProvider],
    *,
    run: Run,
    today: date,
    artist_graph: ArtistGraph | None = None,
) -> DiscoverySummary:
    profile = get_profile(session, profile_id)
    # The artist graph runs before the terms, so a night rich in one still gets all of the other.
    found = _discover_from_artists(session, profile, artist_graph, run, today) if artist_graph else None
    active_terms = sorted(
        (term for term in profile.search_terms if term.status == SearchTermStatus.ACTIVE),
        key=lambda term: term.term.lower(),
    )
    outcomes = tuple(_discover_term(session, term, providers, run, today) for term in active_terms)

    record_stage(
        session,
        run,
        STAGE,
        count_in=sum(outcome.candidates for outcome in outcomes) + (found.playlists if found else 0),
        count_out=sum(outcome.new + outcome.requeued for outcome in outcomes)
        + (found.new + found.requeued if found else 0),
        profile_id=profile.id,
    )
    return DiscoverySummary(profile_id=profile.id, run_id=run.id, terms=outcomes, discovered_on=found)


def placeholder_name(title: str) -> str:
    """A search result title without the engine's "- playlist by X | Spotify" decoration."""
    name = PLAYLIST_BY_SUFFIX.sub("", SPOTIFY_SUFFIX.sub("", title.strip())).strip()
    return (name or UNTITLED)[:MAX_PLAYLIST_NAME_LENGTH]


# --- The funnel both sources go through --------------------------------------------------------


def _admit(session: Session, spotify_id: str, name: str, skip: Container[str]) -> str:
    """Put one discovered id through the candidate funnel, and say which way it went.

    Both sources share this, which is the point: a playlist found through an artist page gets
    precisely the treatment one found through search gets, including the re-check windows and the
    exclusion rules, and there is no second funnel to keep in step with this one.
    """
    playlist = session.get(Playlist, spotify_id)
    if playlist is None:
        session.add(Playlist(spotify_id=spotify_id, name=name, status=PlaylistStatus.CANDIDATE))
        outcome = NEW
    elif spotify_id in skip:
        outcome = SKIPPED
    else:
        playlist.status = PlaylistStatus.CANDIDATE
        playlist.rejection_reason = None
        outcome = REQUEUED
    session.flush()  # the playlist row must exist before its sources reference it
    return outcome


# --- Search ------------------------------------------------------------------------------------


def _discover_term(
    session: Session, term: SearchTerm, providers: Sequence[SearchProvider], run: Run, today: date
) -> TermOutcome:
    result = discover(term.term, providers)
    skip = playlist_ids_to_skip(session, [candidate.spotify_id for candidate in result.candidates], today)
    counts: Counter[str] = Counter()

    for candidate in result.candidates:
        name = placeholder_name(candidate.hits[0].title)
        counts[_admit(session, candidate.spotify_id, name, skip)] += 1
        _record_sources(session, candidate, term, run)

    return TermOutcome(
        term_id=term.id,
        term=term.term,
        candidates=len(result.candidates),
        new=counts[NEW],
        requeued=counts[REQUEUED],
        skipped=counts[SKIPPED],
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


# --- The artist graph ----------------------------------------------------------------------------


def _discover_from_artists(
    session: Session, profile: Profile, client: ArtistGraph, run: Run, today: date
) -> DiscoveredOnOutcome:
    """Read each reference artist's "Discovered on" section and funnel what it names.

    Artists are taken in name order so that the report, and the rank a repeated playlist keeps,
    come out the same way twice.
    """
    artists = sorted(profile.reference_artists, key=lambda artist: artist.display_name.lower())
    # Once per artist ever, not nightly: an artist already carrying an id is skipped before any
    # fetch, which keeps the recurring cost at one page load per *new* reference artist.
    resolve_missing_artist_ids(session, artists, client)

    ranks: dict[str, int] = {}
    errors: list[str] = []
    read = 0
    for artist in artists:
        if not artist.spotify_artist_id:
            continue
        try:
            overview = client.fetch_artist_overview(artist.spotify_artist_id)
        except Exception as error:
            # An artist page that times out, gets blocked or changed shape costs that artist's
            # contribution. The other artists, the search terms and the night all carry on.
            errors.append(f"{artist.display_name}: {type(error).__name__}: {error}")
            continue
        read += 1
        for rank, spotify_id in enumerate(discovered_on_playlist_ids(overview), start=1):
            # Spotify's order is a ranking. The first artist to name a playlist keeps its rank.
            ranks.setdefault(spotify_id, rank)

    skip = playlist_ids_to_skip(session, list(ranks), today)
    counts: Counter[str] = Counter()
    for spotify_id, rank in ranks.items():
        # No title to borrow, unlike a search hit: here we have an id and nothing else until
        # the fetch stage replaces this with the playlist's real name.
        counts[_admit(session, spotify_id, UNTITLED, skip)] += 1
        _record_discovered_on(session, spotify_id, rank, run)

    return DiscoveredOnOutcome(
        artists=read,
        playlists=len(ranks),
        new=counts[NEW],
        requeued=counts[REQUEUED],
        skipped=counts[SKIPPED],
        unresolved=tuple(artist.display_name for artist in artists if not artist.spotify_artist_id),
        errors=tuple(errors),
    )


def _record_discovered_on(session: Session, spotify_id: str, rank: int, run: Run) -> None:
    """Attribute a playlist to the artist graph: a provider, and no search term, because there is none.

    `search_term_id` is nullable, and `uq_playlist_sources_origin` is NULLS NOT DISTINCT -- two
    null terms therefore count as *equal*, so one playlist, one run and this provider is one row
    and a second insert is refused. That was measured against Postgres rather than assumed: under
    its default (NULLS DISTINCT) the second row would have been accepted and the same playlist
    attributed twice a night. So the `on_conflict_do_nothing()` the search half already uses is
    what turns that refusal into a silent no-op, and re-running inside one run stays harmless.
    """
    session.execute(
        insert(PlaylistSource)
        .values(
            playlist_id=spotify_id,
            run_id=run.id,
            provider=SourceProvider.DISCOVERED_ON,
            search_term_id=None,
            rank=rank,
        )
        .on_conflict_do_nothing()
    )
