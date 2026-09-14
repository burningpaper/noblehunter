"""A playlist carrying the profile's reference artists ranks above one that matched on genre alone.

Jarred's choice (2026-09-14): genre-word and Claude matches fill the digest, but never jump ahead
of a playlist that already has his reference artists on it, whatever its contact or size.
"""

from core.models import PlaylistProfileFit
from tests.test_digest import active_profile, digest_tonight, entries_today, ready_lead


def genre_only(session, profile, **kwargs):
    lead = ready_lead(session, profile, **kwargs)
    fit = session.get(PlaylistProfileFit, (lead.spotify_id, profile.id))
    fit.reference_artists_present = []
    fit.genre_tags = ["IDM"]
    session.flush()
    return lead


def test_an_artist_match_beats_a_genre_match_even_with_a_weaker_contact(session):
    profile = active_profile(session, digest_target=1)
    genre_only(session, profile, fit=0.3, grade="A", days_since_add=0, followers=5000)
    artist_match = ready_lead(session, profile, fit=0.34, grade="B", days_since_add=40, followers=300)

    digest_tonight(session)

    assert [entry.playlist_id for entry in entries_today(session)] == [artist_match.spotify_id]


def test_genre_matches_still_fill_the_rest_of_the_digest(session):
    profile = active_profile(session, digest_target=3)
    artist_match = ready_lead(session, profile, fit=0.34)
    better_genre = genre_only(session, profile, fit=0.3, days_since_add=1)
    weaker_genre = genre_only(session, profile, fit=0.3, days_since_add=50)

    digest_tonight(session)

    assert [entry.playlist_id for entry in entries_today(session)] == [
        artist_match.spotify_id,
        better_genre.spotify_id,
        weaker_genre.spotify_id,
    ]
