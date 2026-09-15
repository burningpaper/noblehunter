"""The evaluate step: fetch each candidate, store what Spotify says, and record the verdict."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from core.models import Curator, Playlist, PlaylistProfileFit, RunStageCount
from core.profiles import create_profile, set_profile_active
from pipeline.evaluate import evaluate_candidates
from pipeline.runs import start_run
from pipeline.spotify import PlaylistData, SpotifyFetchError, Track
from tests.factories import default_artist_id, make_playlist
from tests.profile_helpers import add_contents

TODAY = date(2026, 9, 14)
NOW = datetime.combine(TODAY, time(3, 0), tzinfo=UTC)


def pid(n: int) -> str:
    return f"{n:0>22}"


def added(days_ago: int) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(12, 0), tzinfo=UTC)


def data(
    spotify_id: str, *, artists=("Artist 0", "Artist 1", "Artist 2"), days=(3, 30, 90), **overrides
) -> PlaylistData:
    tracks = tuple(
        Track(uid=f"{spotify_id}-{i}", name=f"Track {i}", artists=(artist,), added_at=added(day))
        for i, (artist, day) in enumerate(zip(artists, days, strict=True))
    )
    fields = {
        "spotify_id": spotify_id,
        "name": "Late Night Machines",
        "description_html": "<b>Glitch</b> &amp; ambient",
        "description_text": "Glitch & ambient",
        "owner_id": "curator1",
        "owner_name": "Mimi Taylor",
        "followers": 1500,
        "total_tracks": 127,
        "cover_image_url": "https://i.scdn.co/image/cover",
        "tracks": tracks,
        "partial": False,
    }
    return PlaylistData(**{**fields, **overrides})


class FakeFetcher:
    def __init__(self, responses: dict[str, PlaylistData | Exception]):
        self.responses = responses
        self.requested: list[str] = []

    def fetch_playlist(self, spotify_id: str) -> PlaylistData:
        self.requested.append(spotify_id)
        response = self.responses[spotify_id]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def profile(session):
    """An active profile whose reference artists are 'Artist 0', 'Artist 1' and 'Artist 2'."""
    profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
    set_profile_active(session, profile.id, True)
    return profile


@pytest.fixture
def run(session):
    return start_run(session, trigger="manual")


def evaluate(session, fetcher, run, **kwargs):
    return evaluate_candidates(session, fetcher, run=run, today=TODAY, now=NOW, **kwargs)


class TestStoring:
    def test_fetched_details_are_stored(self, session, profile, run):
        make_playlist(session, spotify_id=pid(1), name="Placeholder", status="candidate")

        evaluate(session, FakeFetcher({pid(1): data(pid(1))}), run)

        stored = session.get(Playlist, pid(1))
        assert stored.name == "Late Night Machines"
        assert stored.description == "Glitch & ambient"
        assert (stored.owner_spotify_id, stored.owner_name) == ("curator1", "Mimi Taylor")
        assert (stored.followers, stored.track_count, stored.size_band) == (1500, 127, "500-2k")
        assert stored.cover_image_url == "https://i.scdn.co/image/cover"
        assert stored.last_add_at == added(3)
        assert stored.last_checked_at == NOW
        assert (stored.is_alive, stored.is_real) == (True, True)

    def test_one_curator_per_spotify_owner(self, session, profile, run):
        for n in (1, 2):
            make_playlist(session, spotify_id=pid(n), status="candidate")

        evaluate(
            session, FakeFetcher({pid(1): data(pid(1)), pid(2): data(pid(2), owner_name="Mimi T.")}), run
        )

        curators = session.scalars(select(Curator)).all()
        assert len(curators) == 1
        assert curators[0].spotify_user_id == "curator1"
        assert curators[0].display_name == "Mimi T."
        assert {p.curator_id for p in session.scalars(select(Playlist))} == {curators[0].id}


class TestVerdicts:
    def test_fitting_playlist_qualifies_with_fit_rows(self, session, profile, run):
        make_playlist(session, spotify_id=pid(1), status="candidate")

        summary = evaluate(session, FakeFetcher({pid(1): data(pid(1))}), run)

        assert session.get(Playlist, pid(1)).status == "qualified"
        fit = session.scalar(select(PlaylistProfileFit).where(PlaylistProfileFit.playlist_id == pid(1)))
        assert (fit.profile_id, fit.qualified) == (profile.id, True)
        assert sorted(fit.reference_artists_present) == ["Artist 0", "Artist 1", "Artist 2"]
        assert summary.qualified == 1

    @pytest.mark.parametrize(
        "overrides, reason",
        [
            ({"days": (200, 300, 400)}, "not-alive"),
            ({"description_text": "Guaranteed placement, small submission fee"}, "not-real"),
            ({"artists": ("Nobody", "Nope", "Nada")}, "no-fit"),
            ({"owner_id": "spotify"}, "spotify-owned"),
        ],
    )
    def test_rejections_record_their_reason(self, session, profile, run, overrides, reason):
        make_playlist(session, spotify_id=pid(1), status="candidate")

        evaluate(session, FakeFetcher({pid(1): data(pid(1), **overrides)}), run)

        stored = session.get(Playlist, pid(1))
        assert (stored.status, stored.rejection_reason) == ("rejected", reason)

    def test_paused_profiles_are_not_judged_against(self, session, profile, run):
        paused = add_contents(session, create_profile(session, default_artist_id(session), "Paused One"))
        make_playlist(session, spotify_id=pid(1), status="candidate")

        evaluate(session, FakeFetcher({pid(1): data(pid(1))}), run)

        profile_ids = session.scalars(select(PlaylistProfileFit.profile_id)).all()
        assert profile_ids == [profile.id]
        assert paused.id not in profile_ids


class TestFailures:
    def test_timeouts_are_marked_for_retry(self, session, profile, run):
        make_playlist(session, spotify_id=pid(1), status="candidate")
        fetcher = FakeFetcher({pid(1): SpotifyFetchError("timeout", "Timed out loading playlist")})

        summary = evaluate(session, fetcher, run)

        assert session.get(Playlist, pid(1)).status == "fetch-failed"
        assert summary.failed == 1
        assert "Timed out" in summary.errors[0]

    def test_missing_playlists_are_treated_as_dead(self, session, profile, run):
        make_playlist(session, spotify_id=pid(1), status="candidate")
        fetcher = FakeFetcher({pid(1): SpotifyFetchError("not_found", "Playlist not found")})

        evaluate(session, fetcher, run)

        stored = session.get(Playlist, pid(1))
        assert (stored.status, stored.rejection_reason) == ("rejected", "not-alive")

    def test_being_blocked_stops_the_run_politely(self, session, profile, run):
        for n in (1, 2, 3):
            make_playlist(session, spotify_id=pid(n), status="candidate")
        fetcher = FakeFetcher(
            {pid(1): data(pid(1)), pid(2): SpotifyFetchError("blocked", "HTTP 429"), pid(3): data(pid(3))}
        )

        summary = evaluate(session, fetcher, run)

        assert summary.blocked is True
        assert pid(3) not in fetcher.requested
        assert session.get(Playlist, pid(3)).status == "candidate"


class TestSelection:
    def test_only_candidates_are_fetched_oldest_first_up_to_the_limit(self, session, profile, run):
        make_playlist(session, spotify_id=pid(1), status="qualified")
        for n in (2, 3, 4):
            make_playlist(session, spotify_id=pid(n), status="candidate")
        session.get(Playlist, pid(4)).first_seen_at = NOW - timedelta(days=2)
        session.flush()
        fetcher = FakeFetcher({pid(n): data(pid(n)) for n in (2, 3, 4)})

        evaluate(session, fetcher, run, limit=2)

        assert fetcher.requested[0] == pid(4)
        assert len(fetcher.requested) == 2
        assert pid(1) not in fetcher.requested

    def test_stage_counts_are_recorded(self, session, profile, run):
        for n in (1, 2):
            make_playlist(session, spotify_id=pid(n), status="candidate")
        fetcher = FakeFetcher({pid(1): data(pid(1)), pid(2): SpotifyFetchError("timeout", "slow")})

        evaluate(session, fetcher, run)

        counts = {
            row.stage: (row.count_in, row.count_out)
            for row in session.scalars(select(RunStageCount).where(RunStageCount.run_id == run.id))
        }
        assert counts["fetch"] == (2, 1)
        assert counts["qualify"] == (1, 1)

    def test_without_an_active_profile_nothing_is_fetched(self, session, run):
        make_playlist(session, spotify_id=pid(1), status="candidate")
        fetcher = FakeFetcher({pid(1): data(pid(1))})

        summary = evaluate(session, fetcher, run)

        assert fetcher.requested == []
        assert "No active profiles" in summary.errors[0]
        assert session.get(Playlist, pid(1)).status == "candidate"

    def test_nothing_to_do_is_fine(self, session, profile, run):
        summary = evaluate(session, FakeFetcher({}), run)

        assert (summary.fetched, summary.qualified) == (0, 0)
        assert session.scalar(select(func.count()).select_from(Playlist)) == 0
