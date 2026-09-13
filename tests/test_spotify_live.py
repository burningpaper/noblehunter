"""Live checks against open.spotify.com, logged out. Slow and polite; run with --run-live.

These use the Stage 0 test IDs, whose shape we know: if they fail, Spotify changed something.
Each test opens its own client: Playwright's sync API allows only one running at a time.
"""

import time

import pytest

from pipeline.spotify import SpotifyFetchError, SpotifyWebClient

pytestmark = pytest.mark.live

MIMI_TAYLOR_PLAYLIST = "3mA6qe4mVO0vDRH0cZM1hJ"
MIMI_TAYLOR_USER = "1251802306"
BIG_PLAYLIST = "2H5eFOIItjgtyYjKinj9ev"  # 521 tracks at Stage 0


def timed(label: str, action):
    started = time.monotonic()
    result = action()
    print(f"\n{label}: {time.monotonic() - started:.1f}s")
    return result


def test_fetch_playlist_returns_every_track():
    with SpotifyWebClient() as client:
        playlist = timed("fetch_playlist (127 tracks)", lambda: client.fetch_playlist(MIMI_TAYLOR_PLAYLIST))

    assert playlist.total_tracks == 127
    assert len(playlist.tracks) == 127
    assert playlist.partial is False
    assert all(track.added_at is not None for track in playlist.tracks)
    assert isinstance(playlist.followers, int)
    assert playlist.owner_id == MIMI_TAYLOR_USER


def test_fetch_user_playlists_returns_only_owned_playlists():
    with SpotifyWebClient() as client:
        playlists = timed("fetch_user_playlists", lambda: client.fetch_user_playlists(MIMI_TAYLOR_USER))

    assert len(playlists) == 17
    assert all(playlist.owner_id == MIMI_TAYLOR_USER for playlist in playlists)


def test_big_playlist_fetches_first_page_and_tail_only():
    with SpotifyWebClient(max_full_tracks=100, tail_tracks=60) as client:
        playlist = timed("fetch_playlist (tail of big playlist)", lambda: client.fetch_playlist(BIG_PLAYLIST))

    assert playlist.partial is True
    assert playlist.total_tracks > 100
    assert len(playlist.tracks) == 25 + 60


def test_missing_playlist_is_not_found_without_waiting_for_a_timeout():
    with SpotifyWebClient(retries=0) as client:
        started = time.monotonic()
        with pytest.raises(SpotifyFetchError) as raised:
            client.fetch_playlist("0000000000000000000000")
        elapsed = time.monotonic() - started
        print(f"\nmissing playlist: {elapsed:.1f}s")

    assert raised.value.kind == "not_found"
    assert elapsed < 20
