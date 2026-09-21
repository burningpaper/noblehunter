"""One real logged-out artist search, so that drift in Spotify's payload is found on purpose.

`searchArtists` is undocumented, and this fetch has a failure mode the overview fetch does not:
the search page could stop firing that operation, or rename it, and every reference artist
would quietly go unresolved. A fixture cannot notice that. This can.

Run it with `pytest --run-live -s` when a profile's artists stop resolving, before assuming the
names are at fault. Skipped in the default run: it opens a browser and talks to Spotify.
"""

import pytest

from pipeline.artist_ids import artist_id_from_search, top_artist
from pipeline.spotify import SpotifyWebClient

pytestmark = pytest.mark.live

APHEX_TWIN = "Aphex Twin"
APHEX_TWIN_ID = "6kBDZFXuLrZgHnvmPu9NsG"  # the id the committed fixtures were captured from


def test_a_real_search_still_resolves_a_known_artist():
    with SpotifyWebClient() as client:
        payload = client.fetch_artist_search(APHEX_TWIN)

    found = top_artist(payload)
    print(f"\nTop hit for {APHEX_TWIN!r}: {found}")

    assert found is not None, "Spotify returned no usable artist -- the payload shape may have moved"
    assert artist_id_from_search(payload, APHEX_TWIN) == APHEX_TWIN_ID
