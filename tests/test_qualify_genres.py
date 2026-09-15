"""Fit without reference artists, part one: the playlist names its genre itself.

Jarred (2026-09-14): requiring one of his reference artists on a playlist is too restrictive. A
playlist that calls itself "IDM" or "glitch" in its title or description is in the right room,
even if none of his listed artists are on it yet. Such a match ranks below a reference-artist
match, and anti-signals still win.
"""

from dataclasses import replace

from core.profile_contents import add_genre
from core.profiles import create_profile
from pipeline.qualify import (
    GENRE_MATCH_SCORE,
    TIER_ACCEPTABLE,
    TIER_GENRE,
    TIER_REJECTED,
    TIER_TOO_SMALL,
    TIER_WEAK,
    ProfileRules,
    assess_fit,
    judge,
)
from tests.factories import default_artist_id
from tests.test_qualify import SYNMAN, TODAY, playlist, track

RULES = replace(SYNMAN, genres=("IDM", "glitch", "ambient techno"))


def with_artist(name: str) -> list:
    return [track(name, days_ago=5, uid=str(i)) for i in range(5)]


def test_a_genre_in_the_name_qualifies_without_reference_artists():
    fit = assess_fit(playlist(name="Late Night IDM"), RULES)

    assert fit.tier == TIER_GENRE
    assert fit.qualifies
    assert fit.matched_genres == ("IDM",)
    assert fit.reference_artists_present == ()


def test_a_genre_in_the_description_counts_too():
    fit = assess_fit(playlist(description_text="Warm glitch and broken beats, updated weekly"), RULES)

    assert fit.matched_genres == ("glitch",)


def test_genre_words_match_whole_words_only():
    fit = assess_fit(playlist(name="Glitched out", description_text="Humid evenings"), RULES)

    assert fit.tier == TIER_WEAK
    assert fit.matched_genres == ()


def test_reference_artists_still_score_above_a_genre_match():
    genre_only = assess_fit(playlist(name="IDM"), RULES)
    one_artist = assess_fit(playlist(tracks=with_artist("Plaid")), RULES)

    assert one_artist.tier == TIER_ACCEPTABLE
    assert genre_only.score == GENRE_MATCH_SCORE
    assert genre_only.score < one_artist.score


def test_artists_and_genre_words_together_are_an_artist_match():
    fit = assess_fit(playlist(name="IDM", tracks=with_artist("Plaid")), RULES)

    assert fit.tier == TIER_ACCEPTABLE
    assert fit.matched_genres == ("IDM",)


def test_anti_signals_still_win_over_genre_words():
    assert assess_fit(playlist(name="IDM & EDM bangers"), RULES).tier == TIER_REJECTED


def test_a_genre_match_below_the_follower_floor_is_too_small():
    assert assess_fit(playlist(name="IDM", followers=10), RULES).tier == TIER_TOO_SMALL


def test_judge_qualifies_an_alive_playlist_on_genre_words():
    verdict = judge(playlist(name="IDM essentials"), [RULES], TODAY)

    assert verdict.status == "qualified"
    assert verdict.qualified_profile_ids == (1,)


def test_a_profile_without_genres_still_needs_its_artists():
    assert assess_fit(playlist(name="IDM"), SYNMAN).tier == TIER_WEAK


def test_rules_carry_the_profiles_genres_in_priority_order(session):
    profile = create_profile(session, default_artist_id(session), "Synman")
    add_genre(session, profile.id, "IDM")
    add_genre(session, profile.id, "glitch")

    assert ProfileRules.from_profile(profile).genres == ("IDM", "glitch")
