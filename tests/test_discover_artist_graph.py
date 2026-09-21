"""Discovery from the artist graph: reference artists' "Discovered on" sections become candidates.

Search ran dry. This is the other pool, and the tests here are about *wiring* it into the step
that already exists -- the parsing and the name resolution are proven next door, in
`test_discovered_on.py` and `test_artist_ids.py`, against captured payloads.

So the fake here answers with payloads shaped the way Spotify shapes them, and the assertions
are all about the funnel: what becomes a candidate, what is attributed to whom, what happens
when the same playlist arrives twice, and what one broken artist page costs. Nothing in this
file touches the network, which is the whole point of injecting the client rather than opening
one -- Playwright's sync API refuses a second browser, so a source that built its own would be
untestable here and would fail at 02:00 instead.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from core.models import Playlist, PlaylistSource, ReferenceArtist, RunStageCount
from core.profile_contents import add_search_term
from core.profiles import create_profile, set_profile_active
from pipeline.discover import discover_for_profile
from pipeline.evaluate import EvaluationSummary
from pipeline.nightly import RunReport, describe_run, run_pipeline
from pipeline.runs import start_run
from pipeline.search import QUERY_PREFIX, ProviderResult, SearchHit
from pipeline.spotify import PlaylistData, SpotifyFetchError, Track
from tests.factories import default_artist_id
from tests.profile_helpers import add_contents

TODAY = date(2026, 9, 14)
NOW = datetime.combine(TODAY, time(3, 0), tzinfo=UTC)


def pid(n: int) -> str:
    """A valid-looking 22-character Spotify playlist id."""
    return f"{n:0>22}"


def aid(n: int) -> str:
    """A valid-looking 22-character Spotify artist id."""
    return f"artistid{n:0>14}"


# --- Payloads, nested the way Spotify nests them -----------------------------------------------


def search_payload(*artists: tuple[str, str]) -> dict:
    items = [
        {"data": {"profile": {"name": name}, "uri": f"spotify:artist:{artist_id}"}}
        for name, artist_id in artists
    ]
    return {"data": {"searchV2": {"artists": {"totalCount": len(items), "items": items}}}}


def overview_payload(*playlist_ids: str) -> dict:
    items = [{"data": {"__typename": "Playlist", "uri": f"spotify:playlist:{p}"}} for p in playlist_ids]
    return {"data": {"artistUnion": {"relatedContent": {"discoveredOnV2": {"items": items}}}}}


class FakeGraph:
    """One Spotify client's worth of artist graph, canned. Remembers everything it was asked."""

    def __init__(
        self,
        artists: dict[str, str] | None = None,
        discovered: dict[str, list[str]] | None = None,
        *,
        search_errors: dict[str, Exception] | None = None,
        overview_errors: dict[str, Exception] | None = None,
    ):
        self.artists = artists or {}  # display name -> the artist id Spotify's search returns
        self.discovered = discovered or {}  # artist id -> its "Discovered on" playlist ids, in order
        self.search_errors = search_errors or {}
        self.overview_errors = overview_errors or {}
        self.searched: list[str] = []
        self.overviews: list[str] = []

    def fetch_artist_search(self, name: str) -> object:
        self.searched.append(name)
        if name in self.search_errors:
            raise self.search_errors[name]
        found = self.artists.get(name)
        return search_payload((name, found)) if found else search_payload()

    def fetch_artist_overview(self, artist_id: str) -> object:
        self.overviews.append(artist_id)
        if artist_id in self.overview_errors:
            raise self.overview_errors[artist_id]
        return overview_payload(*self.discovered.get(artist_id, []))


class FakeProvider:
    """The search half, canned: {term: [(spotify_id, rank, title), ...]}."""

    name = "serper"

    def __init__(self, results: dict[str, list[tuple[str, int, str]]] | None = None):
        self.results = results or {}
        self.queries: list[str] = []

    def search(self, query: str) -> ProviderResult:
        self.queries.append(query)
        term = query.removeprefix(QUERY_PREFIX)
        return ProviderResult(
            hits=tuple(SearchHit(sid, self.name, rank, title, "") for sid, rank, title in self.results[term])
            if term in self.results
            else ()
        )


class FakeFetcher:
    """Enough of a playlist for `evaluate` to judge, so the nightly tests can run end to end."""

    def fetch_playlist(self, spotify_id: str) -> PlaylistData:
        tracks = tuple(
            Track(uid=f"{spotify_id}-{i}", name="t", artists=("Artist 0",), added_at=NOW - timedelta(days=d))
            for i, d in enumerate((2, 20, 50))
        )
        return PlaylistData(
            spotify_id=spotify_id,
            name="Real name",
            description_html="",
            description_text="",
            owner_id="curator1",
            owner_name="A Curator",
            followers=800,
            total_tracks=40,
            cover_image_url=None,
            tracks=tracks,
            partial=False,
        )


# --- Fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def profile(session):
    """A profile with one search term and no reference artists; tests add the artists they need."""
    profile = create_profile(session, default_artist_id(session), "Synman")
    add_search_term(session, profile.id, "glitchy ambient")
    return profile


@pytest.fixture
def run(session):
    return start_run(session, trigger="manual")


def add_artist(session, profile, display_name: str, spotify_artist_id: str | None = None) -> ReferenceArtist:
    artist = ReferenceArtist(display_name=display_name, spotify_artist_id=spotify_artist_id)
    profile.reference_artists.append(artist)
    session.flush()
    return artist


def sources_for(session, spotify_id: str) -> list[tuple[str, int | None, int | None]]:
    rows = session.scalars(select(PlaylistSource).where(PlaylistSource.playlist_id == spotify_id))
    return sorted((row.provider, row.search_term_id, row.rank) for row in rows)


def at(day: date) -> datetime:
    return datetime.combine(day, time(2, 0), tzinfo=UTC)


def report_for(summary, profile) -> str:
    """The run report for a discovery summary alone, with an empty evaluation behind it."""
    return describe_run(
        RunReport(
            run_id=summary.run_id,
            discoveries=(summary,),
            evaluation=EvaluationSummary(0, 0, 0, 0, blocked=False, errors=()),
            profile_names={profile.id: profile.name},
        )
    )


# --- The funnel --------------------------------------------------------------------------------


class TestPlaylistsFromTheArtistGraph:
    def test_ids_from_an_artist_page_become_candidates(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1), pid(2)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert session.get(Playlist, pid(1)).status == "candidate"
        assert session.get(Playlist, pid(2)).status == "candidate"
        assert (summary.discovered_on.artists, summary.discovered_on.playlists) == (1, 2)
        assert summary.discovered_on.new == 2

    def test_they_are_attributed_to_the_provider_with_no_search_term(self, session, profile, run):
        """A discovered-on playlist has no search term, and the column is nullable for this."""
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert sources_for(session, pid(1)) == [("discovered-on", None, 1)]

    def test_spotifys_own_order_is_kept_as_the_rank(self, session, profile, run):
        """ "Discovered on" is ranked -- most-arrived-through first -- and nothing downstream
        could recover that order once it is thrown away."""
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1), pid(2), pid(3)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert [sources_for(session, pid(n))[0][2] for n in (1, 2, 3)] == [1, 2, 3]

    def test_a_placeholder_name_stands_in_until_the_playlist_is_fetched(self, session, profile, run):
        """Discovery has an id and nothing else here -- no title to borrow, unlike a search hit."""
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert session.get(Playlist, pid(1)).name == "Untitled playlist"

    def test_the_search_terms_still_run_beside_it(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1)]})
        serper = FakeProvider({"glitchy ambient": [(pid(2), 1, "Late Air")]})

        summary = discover_for_profile(
            session, profile.id, [serper], run=run, today=TODAY, artist_graph=graph
        )

        assert sources_for(session, pid(1)) == [("discovered-on", None, 1)]
        assert sources_for(session, pid(2))[0][0] == "serper"
        assert (summary.discovered_on.new, summary.terms[0].new) == (1, 1)
        assert summary.queued == 2

    def test_the_stage_counts_include_both_sources(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1)]})
        serper = FakeProvider({"glitchy ambient": [(pid(2), 1, "Late Air")]})

        discover_for_profile(session, profile.id, [serper], run=run, today=TODAY, artist_graph=graph)

        row = session.scalar(select(RunStageCount).where(RunStageCount.run_id == run.id))
        assert (row.count_in, row.count_out) == (2, 2)


class TestResolvingTheArtists:
    def test_an_artist_without_an_id_is_looked_up_and_the_id_stored(self, session, profile, run):
        artist = add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert artist.spotify_artist_id == aid(1)
        assert graph.searched == ["Aphex Twin"]

    def test_an_artist_that_already_has_an_id_is_never_searched_for_again(self, session, profile, run):
        """Resolution is once per artist ever, not nightly: that is the recurring cost of this."""
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert graph.searched == []
        assert graph.overviews == [aid(1)]

    def test_an_artist_that_cannot_be_resolved_has_no_page_read(self, session, profile, run):
        add_artist(session, profile, "discolines")
        add_artist(session, profile, "Aphex Twin")
        graph = FakeGraph({"Aphex Twin": aid(1)}, {aid(1): [pid(1)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert graph.overviews == [aid(1)]
        assert summary.discovered_on.unresolved == ("discolines",)
        assert summary.discovered_on.artists == 1  # only the one whose page was actually read

    def test_a_top_hit_that_is_another_act_stores_nothing(self, session, profile, run):
        """A wrong id would be stored for ever and quietly pollute this profile every night.

        Spotify calls this act "A Winged Victory for the Sullen", and it is one of the three real
        reference artists that genuinely do not resolve. No page is read for it, and the report
        says whose name to fix.
        """
        artist = add_artist(session, profile, "Winged Victory for the Sullen")

        class TopHitIsSomebodyElse(FakeGraph):
            def fetch_artist_search(self, name: str) -> object:
                self.searched.append(name)
                return search_payload(("A Winged Victory for the Sullen", aid(2)))

        graph = TopHitIsSomebodyElse()

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert artist.spotify_artist_id is None
        assert summary.discovered_on.unresolved == ("Winged Victory for the Sullen",)
        assert graph.overviews == []

    def test_a_profile_with_no_reference_artists_contributes_nothing_quietly(self, session, profile, run):
        graph = FakeGraph()

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        found = summary.discovered_on
        assert (found.artists, found.playlists, found.new, found.unresolved) == (0, 0, 0, ())
        assert graph.searched == [] and graph.overviews == []


class TestOnePlaylistFoundTwice:
    """The spike saw 114 mentions collapse to 96 unique playlists, so this is the common case."""

    def test_two_artists_naming_one_playlist_make_one_candidate(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        add_artist(session, profile, "Autechre", spotify_artist_id=aid(2))
        graph = FakeGraph({}, {aid(1): [pid(1)], aid(2): [pid(1), pid(2)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert summary.discovered_on.playlists == 2  # unique, not 3 mentions
        assert summary.discovered_on.new == 2
        assert session.scalar(select(func.count()).select_from(Playlist)) == 2

    def test_two_artists_naming_one_playlist_make_one_source_row(self, session, profile, run):
        """One row, keeping the rank of the first artist to name it.

        `playlist_sources` has no column for which artist found a playlist, so a second row
        could only ever be a duplicate of the first. The unique index agrees, and would collapse
        them even if this step did not.
        """
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        add_artist(session, profile, "Autechre", spotify_artist_id=aid(2))
        graph = FakeGraph({}, {aid(1): [pid(9), pid(1)], aid(2): [pid(1)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert sources_for(session, pid(1)) == [("discovered-on", None, 2)]

    def test_the_unique_index_is_what_makes_two_null_terms_the_same_row(self, session, profile, run):
        """Checked rather than assumed, because the whole attribution design rests on it.

        `uq_playlist_sources_origin` is NULLS NOT DISTINCT, so two null `search_term_id` values
        count as *equal*: one playlist, one run, one provider, no term is one row and a second
        insert is refused. Under Postgres's default (NULLS DISTINCT) it would have been accepted
        and the same playlist would be attributed twice a night. `on_conflict_do_nothing()` is
        what turns that refusal into a silent no-op in the step itself.
        """
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})
        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                insert(PlaylistSource).values(
                    playlist_id=pid(1),
                    run_id=run.id,
                    provider="discovered-on",
                    search_term_id=None,
                    rank=2,
                )
            )

    def test_running_again_in_the_same_run_adds_no_duplicate_sources(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)
        discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert session.scalar(select(func.count()).select_from(PlaylistSource)) == 1


class TestPlaylistsWeAlreadyKnow:
    """Exactly the treatment the search half gives them -- it is literally the same funnel."""

    def test_one_due_for_a_recheck_goes_back_in_the_queue(self, session, profile, run):
        session.add(
            Playlist(
                spotify_id=pid(1),
                name="Came back to life",
                status="rejected",
                rejection_reason="not-alive",
                last_checked_at=at(TODAY - timedelta(days=100)),
            )
        )
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        playlist = session.get(Playlist, pid(1))
        assert (playlist.status, playlist.rejection_reason) == ("candidate", None)
        assert (summary.discovered_on.requeued, summary.discovered_on.new) == (1, 0)

    def test_an_excluded_one_stays_put_but_the_attribution_is_still_kept(self, session, profile, run):
        session.add(
            Playlist(
                spotify_id=pid(1),
                name="Pay to play",
                status="rejected",
                rejection_reason="not-real",
                last_checked_at=at(TODAY),
            )
        )
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert session.get(Playlist, pid(1)).status == "rejected"
        assert summary.discovered_on.skipped == 1
        assert sources_for(session, pid(1)) == [("discovered-on", None, 1)]


class TestOneArtistFailingIsNotTheRunsProblem:
    """The difference between a bad night and no digest."""

    def test_a_failing_page_costs_that_artist_and_no_other(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        add_artist(session, profile, "Boards of Canada", spotify_artist_id=aid(2))
        add_artist(session, profile, "Autechre", spotify_artist_id=aid(3))
        graph = FakeGraph(
            {},
            {aid(1): [pid(1)], aid(2): [pid(2)], aid(3): [pid(3)]},
            overview_errors={aid(2): SpotifyFetchError("timeout", "Artist page: timed out")},
        )

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert session.get(Playlist, pid(1)) is not None
        assert session.get(Playlist, pid(2)) is None
        assert session.get(Playlist, pid(3)) is not None
        assert summary.discovered_on.artists == 2
        assert "Boards of Canada" in summary.discovered_on.errors[0]
        assert "timed out" in summary.discovered_on.errors[0]

    def test_the_search_terms_still_run_when_every_artist_page_fails(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph(
            {}, {}, overview_errors={aid(1): SpotifyFetchError("blocked", "Artist page: blocked")}
        )
        serper = FakeProvider({"glitchy ambient": [(pid(2), 1, "Late Air")]})

        summary = discover_for_profile(
            session, profile.id, [serper], run=run, today=TODAY, artist_graph=graph
        )

        assert serper.queries == [f"{QUERY_PREFIX}glitchy ambient"]
        assert session.get(Playlist, pid(2)).status == "candidate"
        assert summary.terms[0].new == 1

    def test_a_page_that_changed_shape_yields_nothing_rather_than_raising(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): []})
        graph.fetch_artist_overview = lambda artist_id: {"errors": [{"message": "PersistedQueryNotFound"}]}

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert (summary.discovered_on.artists, summary.discovered_on.playlists) == (1, 0)
        assert summary.discovered_on.errors == ()

    def test_an_unexpected_error_is_caught_too(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {}, overview_errors={aid(1): ValueError("the browser is on fire")})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert "browser is on fire" in summary.discovered_on.errors[0]
        assert summary.discovered_on.artists == 0


# --- Off by default ----------------------------------------------------------------------------


class TestWithNoArtistGraph:
    """Every existing caller passes none, and must behave exactly as it did before."""

    def test_the_source_does_not_run(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        serper = FakeProvider({"glitchy ambient": [(pid(2), 1, "Late Air")]})

        summary = discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert summary.discovered_on is None
        assert summary.queued == 1
        assert {row.provider for row in session.scalars(select(PlaylistSource))} == {"serper"}

    def test_the_report_says_nothing_about_it(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        serper = FakeProvider({"glitchy ambient": [(pid(2), 1, "Late Air")]})

        summary = discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert "Discovered on" not in report_for(summary, profile)

    def test_a_whole_run_still_works_without_one(self, session):
        profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
        set_profile_active(session, profile.id, True)

        report = run_pipeline(
            session,
            providers=[FakeProvider({"term 0": [(pid(1), 1, "One")]})],
            fetcher=FakeFetcher(),
            trigger="manual",
            today=TODAY,
            now=NOW,
        )

        assert report.discoveries[0].discovered_on is None
        assert report.discoveries[0].queued == 1


def test_a_run_threads_the_artist_graph_through_to_discovery(session):
    profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
    set_profile_active(session, profile.id, True)
    graph = FakeGraph({"Artist 0": aid(1)}, {aid(1): [pid(5)]})

    report = run_pipeline(
        session,
        providers=[FakeProvider()],
        fetcher=FakeFetcher(),
        trigger="manual",
        today=TODAY,
        now=NOW,
        artist_graph=graph,
    )

    assert report.discoveries[0].discovered_on.new == 1
    assert session.get(Playlist, pid(5)).status == "qualified"  # it went all the way through


# --- The report --------------------------------------------------------------------------------


class TestTheReportLine:
    def test_it_names_the_artists_the_playlists_and_the_new_ones(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        add_artist(session, profile, "Autechre", spotify_artist_id=aid(2))
        graph = FakeGraph({}, {aid(1): [pid(1), pid(2)], aid(2): [pid(3)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert "  Discovered on: 2 artists, 3 playlists, 3 new" in report_for(summary, profile)

    def test_it_comes_before_the_search_terms(self, session, profile, run):
        """Discovered-on runs first, so a night that finds plenty there still runs every term."""
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})
        serper = FakeProvider({"glitchy ambient": [(pid(2), 1, "Late Air")]})

        summary = discover_for_profile(
            session, profile.id, [serper], run=run, today=TODAY, artist_graph=graph
        )

        lines = report_for(summary, profile).splitlines()
        assert lines.index("  Discovered on: 1 artists, 1 playlists, 1 new") < lines.index(
            "  glitchy ambient: 1 found, 1 new, 0 requeued, 0 skipped"
        )

    def test_it_names_the_artists_with_no_spotify_id(self, session, profile, run):
        """A silent zero would be indistinguishable from the feature not running at all.

        Each of these is one edit in the profile editor, so the report has to say which.
        """
        add_artist(session, profile, "Clark")
        add_artist(session, profile, "Winged Victory for the Sullen")
        add_artist(session, profile, "discolines")
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert "    no Spotify id yet: Clark, discolines, Winged Victory for the Sullen" in report_for(
            summary, profile
        )

    def test_it_says_nothing_about_ids_when_every_artist_resolved(self, session, profile, run):
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        assert "no Spotify id yet" not in report_for(summary, profile)

    def test_a_failed_artist_page_is_named_in_the_report(self, session, profile, run):
        add_artist(session, profile, "Boards of Canada", spotify_artist_id=aid(2))
        graph = FakeGraph(
            {}, {}, overview_errors={aid(2): SpotifyFetchError("timeout", "Artist page: timed out")}
        )

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        report = report_for(summary, profile)
        assert "  Discovered on: 0 artists, 0 playlists, 0 new" in report
        assert "    error: Boards of Canada: " in report

    def test_a_profile_with_no_terms_still_gets_its_discovered_on_line(self, session, run):
        """The "no active search terms" line used to mean "this profile found nothing"."""
        profile = create_profile(session, default_artist_id(session), "Fresh")
        add_artist(session, profile, "Aphex Twin", spotify_artist_id=aid(1))
        graph = FakeGraph({}, {aid(1): [pid(1)]})

        summary = discover_for_profile(session, profile.id, [], run=run, today=TODAY, artist_graph=graph)

        report = report_for(summary, profile)
        assert "  Discovered on: 1 artists, 1 playlists, 1 new" in report
        assert "  no active search terms" in report
