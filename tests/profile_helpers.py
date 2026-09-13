"""Builders for profiles in database tests."""

from core.models import Profile, ProfileGenre, ProfileTrack, ReferenceArtist, SearchTerm

TRACK_URL = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"


def add_contents(
    session, profile: Profile, *, genres: int = 1, artists: int = 3, tracks: int = 1, terms: int = 5
):
    """Give a profile exactly the requested number of each ingredient (defaults make it activatable)."""
    profile.genres.extend(ProfileGenre(tag=f"Genre {i}", priority=i) for i in range(genres))
    profile.reference_artists.extend(ReferenceArtist(display_name=f"Artist {i}") for i in range(artists))
    profile.tracks.extend(
        ProfileTrack(title=f"Track {i}", spotify_url=f"{TRACK_URL[:-1]}{i}") for i in range(tracks)
    )
    profile.search_terms.extend(
        SearchTerm(term=f"term {i}", origin="manual", status="active") for i in range(terms)
    )
    session.flush()
    return profile
