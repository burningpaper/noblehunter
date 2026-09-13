"""The discover step: turn a profile's search terms into candidate playlists, without repeating work."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from core.models import Playlist, PlaylistSource, RunStageCount
from core.profile_contents import add_search_term, set_search_term_status
from core.profiles import create_profile
from pipeline.discover import discover_for_profile, placeholder_name
from pipeline.runs import start_run
from pipeline.search import QUERY_PREFIX, ProviderResult, SearchHit
from tests.factories import make_playlist

TODAY = date(2026, 9, 14)


def pid(n: int) -> str:
    return f"{n:0>22}"


class FakeProvider:
    """Returns canned hits per term: {term: [(spotify_id, rank, title), ...]}."""

    def __init__(
        self, name: str, results: dict[str, list[tuple[str, int, str]]], errors: tuple[str, ...] = ()
    ):
        self.name = name
        self.results = results
        self.errors = errors
        self.queries: list[str] = []

    def search(self, query: str) -> ProviderResult:
        self.queries.append(query)
        term = query.removeprefix(QUERY_PREFIX)
        hits = tuple(
            SearchHit(sid, self.name, rank, title, "") for sid, rank, title in self.results.get(term, [])
        )
        return ProviderResult(hits=hits, errors=self.errors)


@pytest.fixture
def profile(session):
    profile = create_profile(session, "Synman")
    add_search_term(session, profile.id, "glitchy ambient")
    return profile


@pytest.fixture
def run(session):
    return start_run(session, trigger="manual")


def sources_for(session, spotify_id: str) -> list[tuple[str, int]]:
    rows = session.scalars(select(PlaylistSource).where(PlaylistSource.playlist_id == spotify_id))
    return sorted((row.provider, row.rank) for row in rows)


def at(day: date) -> datetime:
    return datetime.combine(day, time(2, 0), tzinfo=UTC)


class TestNewCandidates:
    def test_new_playlists_become_candidates_with_their_sources(self, session, profile, run):
        serper = FakeProvider(
            "serper", {"glitchy ambient": [(pid(1), 1, "Glitchy Ambient - playlist by Mimi | Spotify")]}
        )
        brave = FakeProvider(
            "brave", {"glitchy ambient": [(pid(1), 3, "Glitchy Ambient"), (pid(2), 1, "Late Air")]}
        )

        summary = discover_for_profile(session, profile.id, [serper, brave], run=run, today=TODAY)

        first = session.get(Playlist, pid(1))
        assert (first.status, first.name) == ("candidate", "Glitchy Ambient")
        assert session.get(Playlist, pid(2)).status == "candidate"
        assert sources_for(session, pid(1)) == [("brave", 3), ("serper", 1)]
        term_id = profile.search_terms[0].id
        assert {s.search_term_id for s in session.scalars(select(PlaylistSource))} == {term_id}
        assert {s.run_id for s in session.scalars(select(PlaylistSource))} == {run.id}
        assert (summary.terms[0].candidates, summary.terms[0].new) == (2, 2)
        assert summary.queued == 2

    def test_only_active_terms_are_searched(self, session, profile, run):
        paused = add_search_term(session, profile.id, "braindance playlist")
        set_search_term_status(session, profile.id, paused.id, "paused")
        serper = FakeProvider("serper", {})

        discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert serper.queries == [f"{QUERY_PREFIX}glitchy ambient"]

    def test_spotify_owned_playlists_never_become_candidates(self, session, profile, run):
        editorial = "37i9dQZF1DXbjZQOVqxNHv"
        serper = FakeProvider("serper", {"glitchy ambient": [(editorial, 1, "IDM Essentials")]})

        discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert session.get(Playlist, editorial) is None

    def test_a_playlist_found_by_two_terms_is_new_only_once(self, session, profile, run):
        add_search_term(session, profile.id, "braindance playlist")
        serper = FakeProvider(
            "serper",
            {"glitchy ambient": [(pid(1), 1, "One")], "braindance playlist": [(pid(1), 2, "One")]},
        )

        summary = discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert sum(outcome.new for outcome in summary.terms) == 1
        assert len(sources_for(session, pid(1))) == 2


class TestKnownPlaylists:
    def test_excluded_playlists_stay_put_but_attribution_is_kept(self, session, profile, run):
        make_playlist(
            session,
            spotify_id=pid(1),
            status="rejected",
            rejection_reason="not-real",
            last_checked_at=at(TODAY),
        )
        serper = FakeProvider("serper", {"glitchy ambient": [(pid(1), 1, "Pay to play")]})

        summary = discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert session.get(Playlist, pid(1)).status == "rejected"
        assert summary.terms[0].skipped == 1
        assert sources_for(session, pid(1)) == [("serper", 1)]

    def test_playlists_due_for_a_recheck_are_queued_again(self, session, profile, run):
        make_playlist(
            session,
            spotify_id=pid(1),
            status="rejected",
            rejection_reason="not-alive",
            last_checked_at=at(TODAY - timedelta(days=100)),
        )
        serper = FakeProvider("serper", {"glitchy ambient": [(pid(1), 1, "Came back to life")]})

        summary = discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        playlist = session.get(Playlist, pid(1))
        assert (playlist.status, playlist.rejection_reason) == ("candidate", None)
        assert summary.terms[0].requeued == 1

    def test_running_again_in_the_same_run_adds_no_duplicate_sources(self, session, profile, run):
        serper = FakeProvider("serper", {"glitchy ambient": [(pid(1), 1, "One")]})

        discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)
        discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        assert session.scalar(select(func.count()).select_from(PlaylistSource)) == 1


class TestReporting:
    def test_provider_errors_are_reported_and_other_results_still_count(self, session, profile, run):
        serper = FakeProvider("serper", {"glitchy ambient": [(pid(1), 1, "One")]})
        brave = FakeProvider("brave", {}, errors=("brave: invalid API key (HTTP 401)",))

        summary = discover_for_profile(session, profile.id, [serper, brave], run=run, today=TODAY)

        assert summary.terms[0].errors == ("brave: invalid API key (HTTP 401)",)
        assert summary.terms[0].new == 1

    def test_stage_counts_are_recorded_for_the_profile(self, session, profile, run):
        make_playlist(session, spotify_id=pid(9), status="candidate")
        serper = FakeProvider("serper", {"glitchy ambient": [(pid(1), 1, "One"), (pid(9), 2, "Nine")]})

        discover_for_profile(session, profile.id, [serper], run=run, today=TODAY)

        row = session.scalar(select(RunStageCount).where(RunStageCount.run_id == run.id))
        assert (row.stage, row.profile_id, row.count_in, row.count_out) == ("discover", profile.id, 2, 1)


@pytest.mark.parametrize(
    "title, expected",
    [
        ("AMBIENT IDM 🤖 Braindance - playlist by Irles Music | Spotify", "AMBIENT IDM 🤖 Braindance"),
        ("IDM Essentials | Spotify Playlist", "IDM Essentials"),
        (
            "Late Night Drive – Alternative Electronic - playlist by Loudarc Music",
            "Late Night Drive – Alternative Electronic",
        ),
        ("  Plain title  ", "Plain title"),
        ("", "Untitled playlist"),
        ("x" * 400, "x" * 300),
    ],
)
def test_placeholder_names_strip_search_engine_decoration(title, expected):
    assert placeholder_name(title) == expected
