"""Evaluate with Claude's fit check: a playlist without the reference artists can still qualify.

Claude is asked only when nothing cheaper decides: the playlist is alive and real, has none of the
reference artists, doesn't name one of the profile's genres, and trips no anti-signal. Its cost
counts toward the nightly budget, and the checks stop once the budget is spent.
"""

from decimal import Decimal

import pytest

from core.app_settings import set_nightly_claude_budget
from core.models import Playlist, PlaylistProfileFit
from core.profile_contents import add_anti_signal
from core.profiles import create_profile, set_profile_active
from pipeline.fit_judge import FitJudgeError, FitOpinion
from pipeline.nightly import describe_run, run_pipeline
from pipeline.qualify import CLAUDE_MATCH_SCORE
from pipeline.runs import start_run
from tests.factories import default_artist_id, make_playlist
from tests.profile_helpers import add_contents
from tests.test_pipeline_evaluate import NOW, TODAY, FakeFetcher, data, evaluate, pid
from tests.test_pipeline_nightly import FakeProvider

STRANGERS = ("Nobody", "Nope", "Nada")


@pytest.fixture
def profile(session):
    """Active; reference artists 'Artist 0-2'; genre 'Genre 0'; search terms 'term 0-4'."""
    profile = add_contents(session, create_profile(session, default_artist_id(session), "Synman"))
    set_profile_active(session, profile.id, True)
    return profile


@pytest.fixture
def run(session):
    return start_run(session, trigger="manual")


class FakeFitJudge:
    def __init__(self, fits: bool = True, error: Exception | None = None, spend: str = "0.004"):
        self.fits = fits
        self.error = error
        self.spend = Decimal(spend)
        self.calls: list[tuple[str, int]] = []

    def judge(self, data, rules) -> FitOpinion:
        self.calls.append((data.spotify_id, rules.profile_id))
        if self.error:
            raise self.error
        return FitOpinion(
            fits=self.fits, genre_tags=("glitch",), reason="Warm, glitchy electronics", spend_usd=self.spend
        )


def candidates(session, *numbers: int) -> None:
    for number in numbers:
        make_playlist(session, spotify_id=pid(number), status="candidate")


class TestClaudeFit:
    def test_claude_can_qualify_a_playlist_without_reference_artists(self, session, profile, run):
        candidates(session, 1)

        summary = evaluate(
            session, FakeFetcher({pid(1): data(pid(1), artists=STRANGERS)}), run, fit_judge=FakeFitJudge()
        )

        assert session.get(Playlist, pid(1)).status == "qualified"
        fit = session.get(PlaylistProfileFit, (pid(1), profile.id))
        assert fit.qualified
        assert fit.fit_score == CLAUDE_MATCH_SCORE
        assert fit.genre_tags == ["glitch"]
        assert fit.reference_artists_present == []
        assert (summary.claude_checks, summary.claude_fits) == (1, 1)
        assert run.llm_spend_usd == Decimal("0.004")

    def test_when_claude_says_no_it_stays_no_fit(self, session, profile, run):
        candidates(session, 1)

        summary = evaluate(
            session,
            FakeFetcher({pid(1): data(pid(1), artists=STRANGERS)}),
            run,
            fit_judge=FakeFitJudge(fits=False),
        )

        stored = session.get(Playlist, pid(1))
        assert (stored.status, stored.rejection_reason) == ("rejected", "no-fit")
        assert (summary.claude_checks, summary.claude_fits) == (1, 0)

    def test_claude_is_not_asked_when_artists_or_genre_words_already_decide(self, session, profile, run):
        candidates(session, 1, 2)
        fetcher = FakeFetcher(
            {pid(1): data(pid(1)), pid(2): data(pid(2), artists=STRANGERS, name="Genre 0 nights")}
        )
        judge = FakeFitJudge()

        evaluate(session, fetcher, run, fit_judge=judge)

        assert judge.calls == []
        assert session.get(Playlist, pid(2)).status == "qualified"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"days": (200, 300, 400)},
            {"description_text": "Guaranteed placement, small submission fee"},
            {"owner_id": "spotify"},
        ],
    )
    def test_claude_is_not_asked_about_dead_fake_or_spotify_playlists(self, session, profile, run, overrides):
        candidates(session, 1)
        judge = FakeFitJudge()

        evaluate(
            session, FakeFetcher({pid(1): data(pid(1), artists=STRANGERS, **overrides)}), run, fit_judge=judge
        )

        assert judge.calls == []

    def test_an_anti_signal_is_not_second_guessed(self, session, profile, run):
        add_anti_signal(session, profile.id, "term", "ambient")
        candidates(session, 1)
        judge = FakeFitJudge()

        evaluate(session, FakeFetcher({pid(1): data(pid(1), artists=STRANGERS)}), run, fit_judge=judge)

        assert judge.calls == []
        assert session.get(Playlist, pid(1)).rejection_reason == "no-fit"

    def test_a_claude_match_below_the_follower_floor_is_too_small(self, session, profile, run):
        candidates(session, 1)

        evaluate(
            session,
            FakeFetcher({pid(1): data(pid(1), artists=STRANGERS, followers=10)}),
            run,
            fit_judge=FakeFitJudge(),
        )

        assert session.get(Playlist, pid(1)).rejection_reason == "too-small"

    def test_claude_checks_stop_at_the_nightly_budget(self, session, profile, run):
        # Budgets are whole cents: one cent covers two $0.006 checks, since the second starts under it.
        set_nightly_claude_budget(session, "0.01")
        candidates(session, 1, 2, 3)
        fetcher = FakeFetcher({pid(n): data(pid(n), artists=STRANGERS) for n in (1, 2, 3)})
        judge = FakeFitJudge(fits=False, spend="0.006")

        summary = evaluate(session, fetcher, run, fit_judge=judge)

        assert len(judge.calls) == 2
        assert summary.claude_checks == 2
        assert session.get(Playlist, pid(3)).rejection_reason == "no-fit"

    def test_a_failing_judge_leaves_it_no_fit_and_says_why(self, session, profile, run):
        candidates(session, 1)
        judge = FakeFitJudge(error=FitJudgeError("Claude couldn't judge the fit (APIConnectionError)"))

        summary = evaluate(
            session, FakeFetcher({pid(1): data(pid(1), artists=STRANGERS)}), run, fit_judge=judge
        )

        assert session.get(Playlist, pid(1)).rejection_reason == "no-fit"
        assert any("couldn't judge" in error for error in summary.errors)


def test_the_nightly_run_hands_the_fit_judge_to_evaluation(session, profile):
    report = run_pipeline(
        session,
        providers=[FakeProvider({"term 0": [pid(1)]})],
        fetcher=FakeFetcher({pid(1): data(pid(1), artists=STRANGERS)}),
        trigger="manual",
        today=TODAY,
        now=NOW,
        fit_judge=FakeFitJudge(),
    )

    assert session.get(Playlist, pid(1)).status == "qualified"
    assert "Claude fit checks: 1, 1 fitted" in describe_run(report)
