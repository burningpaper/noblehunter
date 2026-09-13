"""run_pipeline: one run = discover for active profiles, then fetch and judge the candidates."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from core.models import Playlist, Run
from core.profile_contents import add_search_term
from core.profiles import create_profile, set_profile_active
from pipeline.nightly import run_pipeline
from pipeline.search import QUERY_PREFIX, ProviderResult, SearchHit
from pipeline.spotify import PlaylistData, Track
from tests.profile_helpers import add_contents

TODAY = date(2026, 9, 14)
NOW = datetime.combine(TODAY, time(3, 0), tzinfo=UTC)


def pid(n: int) -> str:
    return f"{n:0>22}"


class FakeProvider:
    name = "serper"

    def __init__(self, results: dict[str, list[str]]):
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str) -> ProviderResult:
        self.queries.append(query)
        term = query.removeprefix(QUERY_PREFIX)
        ids = self.results.get(term, [])
        return ProviderResult(
            hits=tuple(
                SearchHit(sid, self.name, rank, f"Playlist {sid[-1]}", "") for rank, sid in enumerate(ids, 1)
            )
        )


class FakeFetcher:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requested: list[str] = []

    def fetch_playlist(self, spotify_id: str) -> PlaylistData:
        self.requested.append(spotify_id)
        if self.error:
            raise self.error
        tracks = tuple(
            Track(
                uid=f"{spotify_id}-{i}",
                name="t",
                artists=(f"Artist {i}",),
                added_at=NOW - timedelta(days=days),
            )
            for i, days in enumerate((2, 20, 60))
        )
        return PlaylistData(
            spotify_id=spotify_id,
            name="Real name",
            description_html="",
            description_text="",
            owner_id="curator1",
            owner_name="Curator",
            followers=800,
            total_tracks=40,
            cover_image_url=None,
            tracks=tracks,
            partial=False,
        )


@pytest.fixture
def synman(session):
    """Active; reference artists 'Artist 0-2'; search terms 'term 0-4'."""
    profile = add_contents(session, create_profile(session, "Synman"))
    set_profile_active(session, profile.id, True)
    return profile


def run(session, providers, fetcher, **kwargs):
    return run_pipeline(
        session, providers=providers, fetcher=fetcher, trigger="manual", today=TODAY, now=NOW, **kwargs
    )


def test_a_run_discovers_then_evaluates(session, synman):
    report = run(session, [FakeProvider({"term 0": [pid(1)]})], FakeFetcher())

    assert session.get(Run, report.run_id).status == "succeeded"
    assert [d.profile_id for d in report.discoveries] == [synman.id]
    assert report.discoveries[0].queued == 1
    assert report.evaluation.qualified == 1
    assert session.get(Playlist, pid(1)).status == "qualified"


def test_paused_profiles_are_not_searched(session, synman):
    paused = create_profile(session, "Paused")
    add_search_term(session, paused.id, "paused term")
    provider = FakeProvider({})

    run(session, [provider], FakeFetcher())

    assert f"{QUERY_PREFIX}paused term" not in provider.queries


def test_a_run_can_be_narrowed_to_one_profile(session, synman):
    other = add_contents(session, create_profile(session, "Other"))
    set_profile_active(session, other.id, True)

    report = run(session, [FakeProvider({})], FakeFetcher(), profile_id=other.id)

    assert [d.profile_id for d in report.discoveries] == [other.id]


def test_narrowing_to_a_paused_profile_is_refused_before_starting(session, synman):
    paused = create_profile(session, "Paused")

    with pytest.raises(ValueError, match="not active"):
        run(session, [FakeProvider({})], FakeFetcher(), profile_id=paused.id)

    assert session.scalars(select(Run)).all() == []


def test_an_unexpected_failure_marks_the_run_failed_and_is_raised(session, synman):
    with pytest.raises(RuntimeError, match="boom"):
        run(session, [FakeProvider({"term 0": [pid(1)]})], FakeFetcher(error=RuntimeError("boom")))

    failed = session.scalar(select(Run).order_by(Run.id.desc()))
    assert failed.status == "failed"
    assert "RuntimeError: boom" in failed.error


def test_the_fetch_limit_is_passed_through(session, synman):
    fetcher = FakeFetcher()

    run(session, [FakeProvider({"term 0": [pid(1), pid(2), pid(3)]})], fetcher, fetch_limit=1)

    assert len(fetcher.requested) == 1
