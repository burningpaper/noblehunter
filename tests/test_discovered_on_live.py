"""One real logged-out artist page, so that drift in Spotify's payload is found on purpose.

`queryArtistOverview` is undocumented and can change shape without notice. Everything else
about "Discovered on" is tested against a captured fixture, which by definition cannot notice
that the live shape has moved. This test can. Run it with `pytest --run-live -s` when
discovery starts finding nothing, before assuming the artists are at fault.

Skipped in the default run: it opens a browser and talks to Spotify.
"""

import pytest

from pipeline.discovered_on import discovered_on_playlist_ids
from pipeline.spotify import SPOTIFY_OWNED_PREFIX, SpotifyWebClient

pytestmark = pytest.mark.live

APHEX_TWIN = "6kBDZFXuLrZgHnvmPu9NsG"  # the artist the committed fixture was captured from


def test_a_real_artist_page_still_carries_discovered_on():
    with SpotifyWebClient() as client:
        payload = client.fetch_artist_overview(APHEX_TWIN)

    assert payload["data"]["artistUnion"]["uri"] == f"spotify:artist:{APHEX_TWIN}"

    playlist_ids = discovered_on_playlist_ids(payload)
    print(f"\n{len(playlist_ids)} pitchable playlists discovered on Aphex Twin: {playlist_ids}")

    assert playlist_ids, "Spotify returned no usable discoveredOnV2 entries -- the shape may have moved"
    assert all(len(playlist_id) == 22 for playlist_id in playlist_ids)
    assert not any(playlist_id.startswith(SPOTIFY_OWNED_PREFIX) for playlist_id in playlist_ids)
