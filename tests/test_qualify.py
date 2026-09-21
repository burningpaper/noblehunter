"""Qualify rules: is a playlist alive, real, and a fit for a profile? Pure functions, no database."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from pipeline.qualify import (
    TIER_OWN_ARTIST,
    TIER_WEAK,
    ProfileRules,
    assess_fit,
    assess_liveness,
    assess_reality,
    judge,
    size_band,
)
from pipeline.spotify import PlaylistData, Track

TODAY = date(2026, 9, 14)
SYNMAN = ProfileRules(
    profile_id=1,
    name="Synman",
    reference_artists=("Aphex Twin", "Boards of Canada", "Autechre", "Plaid"),
    anti_artists=("Lofi Girl",),
    anti_terms=("lo-fi study beats", "EDM"),
)


def added(days_ago: int) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(12, 0), tzinfo=UTC)


def track(*artists: str, days_ago: int | None = 10, uid: str | None = None) -> Track:
    return Track(
        uid=uid or f"uid-{artists}-{days_ago}",
        name="A track",
        artists=tuple(artists),
        added_at=None if days_ago is None else added(days_ago),
    )


def playlist(tracks=None, **overrides) -> PlaylistData:
    fields = {
        "spotify_id": "0" * 22,
        "name": "Late Night Machines",
        "description_html": "",
        "description_text": "",
        "owner_id": "curator1",
        "owner_name": "A Curator",
        "followers": 900,
        "total_tracks": 80,
        "cover_image_url": None,
        "tracks": tuple(
            tracks if tracks is not None else [track("Someone", days_ago=5, uid=str(i)) for i in range(5)]
        ),
        "partial": False,
    }
    return PlaylistData(**{**fields, **overrides})


class TestLiveness:
    def test_recent_and_regular_adds_are_alive(self):
        result = assess_liveness([track(days_ago=10), track(days_ago=40), track(days_ago=150)], TODAY)

        assert result.alive is True
        assert result.last_add_at == added(10)
        assert result.adds_last_180_days == 3

    def test_last_add_more_than_60_days_ago_is_dead(self):
        tracks = [track(days_ago=61), track(days_ago=70), track(days_ago=80), track(days_ago=90)]

        assert assess_liveness(tracks, TODAY).alive is False

    def test_exactly_60_days_still_counts(self):
        tracks = [track(days_ago=60), track(days_ago=70), track(days_ago=80)]

        assert assess_liveness(tracks, TODAY).alive is True

    def test_too_few_adds_in_six_months_is_dead(self):
        tracks = [track(days_ago=5), track(days_ago=20), track(days_ago=400)]

        result = assess_liveness(tracks, TODAY)

        assert result.alive is False
        assert result.adds_last_180_days == 2

    def test_no_dates_at_all_is_dead(self):
        result = assess_liveness([track(days_ago=None)], TODAY)

        assert (result.alive, result.last_add_at) == (False, None)


class TestReality:
    @pytest.mark.parametrize(
        "description",
        [
            "Submission fee $5, guaranteed placement within 48h",
            "Paid placement available. DM for promo packages",
            "We guarantee streams! Pay to get added.",
        ],
    )
    def test_pay_to_play_descriptions_are_not_real(self, description):
        result = assess_reality(playlist(description_text=description))

        assert result.real is False
        assert any("pay-to-play" in reason for reason in result.reasons)

    def test_mentioning_submithub_alone_is_fine(self):
        assert assess_reality(playlist(description_text="Submit tracks via SubmitHub")).real is True

    def test_huge_following_on_a_tiny_playlist_is_suspicious(self):
        result = assess_reality(playlist(followers=80_000, total_tracks=12))

        assert result.real is False
        assert any("followers" in reason for reason in result.reasons)

    def test_large_real_playlist_is_fine(self):
        assert assess_reality(playlist(followers=80_000, total_tracks=300)).real is True


class TestFit:
    def test_three_reference_artists_is_top_tier(self):
        tracks = [
            track("Aphex Twin"),
            track("autechre", "Someone"),
            track("Boards  of Canada"),
            track("Unknown"),
        ]

        fit = assess_fit(playlist(tracks), SYNMAN)

        assert fit.tier == "top"
        assert fit.reference_artists_present == ("Aphex Twin", "Autechre", "Boards of Canada")

    @pytest.mark.parametrize(
        "artists, tier", [(["Plaid"], "acceptable"), (["Plaid", "Autechre"], "acceptable"), ([], "weak")]
    )
    def test_fewer_reference_artists(self, artists, tier):
        tracks = [track(name) for name in artists] + [track("Unknown")]

        assert assess_fit(playlist(tracks), SYNMAN).tier == tier

    def test_anti_signal_artist_rejects_outright(self):
        tracks = [track("Aphex Twin"), track("Autechre"), track("Plaid"), track("Lofi Girl")]

        fit = assess_fit(playlist(tracks), SYNMAN)

        assert fit.tier == "rejected"
        assert "Lofi Girl" in fit.anti_signals_found

    @pytest.mark.parametrize("name", ["EDM Bangers 2026", "Chill lo-fi study beats"])
    def test_anti_signal_terms_in_name_reject(self, name):
        assert assess_fit(playlist([track("Aphex Twin")], name=name), SYNMAN).tier == "rejected"

    def test_anti_signal_terms_in_description_reject(self):
        fit = assess_fit(playlist([track("Aphex Twin")], description_text="Pure EDM energy"), SYNMAN)

        assert fit.tier == "rejected"

    def test_terms_match_whole_words_only(self):
        assert assess_fit(playlist([track("Plaid")], name="Sounds of Edmonton"), SYNMAN).tier == "acceptable"

    def test_score_grows_with_reference_artists(self):
        one = assess_fit(playlist([track("Plaid")]), SYNMAN).score
        three = assess_fit(playlist([track("Plaid"), track("Autechre"), track("Aphex Twin")]), SYNMAN).score

        assert 0 < one < three <= 1


class TestPlaylistsOwnedByTheProfilesOwnArtists:
    """Pitching Synman to Bonobo for a place on Bonobo's own playlist is not a lead.

    The 2026-09-20 spike qualified exactly that, twice, which is why this rule exists. It lives
    here rather than in discovery because discovery has only a playlist id: the owner is not
    known until the playlist has been fetched.
    """

    def test_a_playlist_owned_by_a_reference_artist_does_not_fit(self):
        fit = assess_fit(playlist([track("Aphex Twin")], owner_name="Aphex Twin"), SYNMAN)

        assert fit.tier == TIER_OWN_ARTIST
        assert fit.qualifies is False

    def test_the_owner_name_is_matched_the_way_every_other_name_here_is(self):
        fit = assess_fit(playlist([track("Plaid")], owner_name="  BOARDS   of canada "), SYNMAN)

        assert fit.tier == TIER_OWN_ARTIST

    def test_somebody_elses_playlist_full_of_those_artists_is_the_whole_point(self):
        tracks = [track("Aphex Twin"), track("Autechre"), track("Plaid")]

        assert assess_fit(playlist(tracks, owner_name="A Curator"), SYNMAN).tier == "top"

    def test_a_playlist_whose_owner_has_no_name_is_not_dropped(self):
        assert assess_fit(playlist([track("Plaid")], owner_name=None), SYNMAN).tier == "acceptable"

    def test_it_is_recorded_as_a_no_fit_rejection(self):
        """There is no rejection reason of its own, and inventing one would mean a migration."""
        tracks = [track("Aphex Twin", days_ago=5), track("Autechre", days_ago=10), track("Plaid")]

        verdict = judge(playlist(tracks, owner_name="Aphex Twin"), [SYNMAN], TODAY)

        assert verdict.status == "rejected"
        assert verdict.rejection_reason == "no-fit"

    def test_claude_is_never_asked_to_reconsider_it(self):
        """`pipeline.evaluate` buys a second opinion only for TIER_WEAK, so this must not be that."""
        fit = assess_fit(playlist([track("Aphex Twin")], owner_name="Aphex Twin"), SYNMAN)

        assert fit.tier != TIER_WEAK


@pytest.mark.parametrize(
    "followers, band",
    [
        (0, "under-500"),
        (499, "under-500"),
        (500, "500-2k"),
        (1999, "500-2k"),
        (2000, "2k-10k"),
        (10_000, "10k-plus"),
        (None, None),
    ],
)
def test_size_band(followers, band):
    assert size_band(followers) == band


class TestJudge:
    def fitting_tracks(self):
        return [
            track("Aphex Twin", days_ago=3, uid="a"),
            track("Autechre", days_ago=30, uid="b"),
            track("Plaid", days_ago=90, uid="c"),
        ]

    def test_spotify_owned_playlists_are_rejected_first(self):
        verdict = judge(playlist(self.fitting_tracks(), owner_id="thesoundsofspotify"), [SYNMAN], TODAY)

        assert (verdict.status, verdict.rejection_reason) == ("rejected", "spotify-owned")

    def test_pay_to_play_beats_a_good_fit(self):
        data = playlist(self.fitting_tracks(), description_text="Guaranteed placement for a small fee")

        assert judge(data, [SYNMAN], TODAY).rejection_reason == "not-real"

    def test_dead_playlists_are_rejected(self):
        tracks = [track("Aphex Twin", days_ago=200, uid="a"), track("Plaid", days_ago=300, uid="b")]

        assert judge(playlist(tracks), [SYNMAN], TODAY).rejection_reason == "not-alive"

    def test_alive_real_and_fitting_one_profile_qualifies(self):
        other = ProfileRules(profile_id=2, name="Other", reference_artists=("Nobody", "Nope", "Nada"))

        verdict = judge(playlist(self.fitting_tracks()), [SYNMAN, other], TODAY)

        assert (verdict.status, verdict.rejection_reason) == ("qualified", None)
        assert verdict.fits[1].tier == "top"
        assert verdict.fits[2].tier == "weak"
        assert verdict.qualified_profile_ids == (1,)

    def test_no_reference_artists_anywhere_is_no_fit(self):
        tracks = [
            track("Unknown", days_ago=3, uid="a"),
            track("Unknown", days_ago=20, uid="b"),
            track("X", days_ago=40, uid="c"),
        ]

        assert judge(playlist(tracks), [SYNMAN], TODAY).rejection_reason == "no-fit"

    def test_verdict_carries_liveness_and_size(self):
        verdict = judge(playlist(self.fitting_tracks(), followers=1500), [SYNMAN], TODAY)

        assert verdict.liveness.last_add_at == added(3)
        assert verdict.size_band == "500-2k"
