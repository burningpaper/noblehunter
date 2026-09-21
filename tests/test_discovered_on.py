"""Reading "Discovered on" out of a real artist page, and surviving one that changed shape.

Nothing here touches the network. The fixture is a trimmed capture of `queryArtistOverview`
for Aphex Twin, so every assertion about Spotify's nesting is an assertion about something
Spotify actually sent.

Two of these tests are the ones worth keeping honest. The section-ignoring test uses a
hand-built payload rather than the fixture, because every `featuringV2` entry in the real
capture is editorial: dropping them proves the prefix filter works, not that the section is
ignored. And the malformed cases all assert "nothing", never "raises" -- an artist page that
changes shape should cost that artist's contribution for one night, not the whole run.
"""

import copy
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from pipeline.discovered_on import discovered_on_playlist_ids

FIXTURES = Path(__file__).parent / "fixtures" / "spotify"
ARTIST_ID = "6kBDZFXuLrZgHnvmPu9NsG"
EDITORIAL_ID = "37i9dQZF1DZ06evO3JKYkV"  # "This Is Aphex Twin", Spotify's own, at position 0
# The four a person actually curates, in the order Spotify listed them. Two editorial entries
# sit among them (positions 0 and 3 of the capture), so asserting this exact list proves the
# filter removes them without disturbing the order of what remains.
PITCHABLE_IDS = [
    "68JXTKfqFZEWO1DQRdVndh",  # Sleep Playlist
    "4IG2L9hS3YqAIy4peznwSZ",  # Best of Aphex Twin
    "5tHLi307wjZY2ePmVQSgkG",  # frutiger aero
    "5XtTnickq6y2jVULZGtdpA",  # Aphex Twin - sleep mix
]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def overview() -> dict:
    return load(f"queryArtistOverview_{ARTIST_ID}.json")


def pid(number: int) -> str:
    """A valid-looking 22-character Spotify playlist ID."""
    return f"playlist{number:0>14}"


def item(spotify_id: str) -> dict:
    return {"data": {"__typename": "Playlist", "uri": f"spotify:playlist:{spotify_id}"}}


def payload_with(*, discovered: Sequence[str] = (), featuring: Sequence[str] = ()) -> dict:
    """A payload nested the way Spotify nests one, carrying exactly these playlists."""
    return {
        "data": {
            "artistUnion": {
                "relatedContent": {
                    "discoveredOnV2": {"items": [item(spotify_id) for spotify_id in discovered]},
                    "featuringV2": {"items": [item(spotify_id) for spotify_id in featuring]},
                }
            }
        }
    }


def sections(payload: dict) -> dict:
    return payload["data"]["artistUnion"]["relatedContent"]


# --- The real capture -------------------------------------------------------------------------


def test_reads_the_pitchable_playlists_from_a_real_artist_page(overview):
    # Two editorial entries sit at positions 0 and 3 of the capture, so this proves the order
    # is Spotify's own *and* that filtering doesn't disturb it -- which the earlier fixture,
    # carrying a single pitchable id, could not.
    assert discovered_on_playlist_ids(overview) == PITCHABLE_IDS


def test_spotifys_own_editorial_playlist_is_dropped(overview):
    # Guard first: if a future trim removes the editorial entry, the assertion below would
    # pass for the wrong reason.
    uris = [(entry.get("data") or {}).get("uri") for entry in sections(overview)["discoveredOnV2"]["items"]]
    assert f"spotify:playlist:{EDITORIAL_ID}" in uris

    assert EDITORIAL_ID not in discovered_on_playlist_ids(overview)


def test_entries_spotify_would_not_expand_are_skipped_rather_than_crashing(overview):
    """Real captures carry `GenericError` entries with no uri at all, and must not be fatal.

    The first capture of this page returned six of eight that way; the second returned two.
    The count is Spotify's mood on the day, so it isn't asserted -- what matters is that such
    entries exist in the fixture at all, and that the parser walks past them.
    """
    items = sections(overview)["discoveredOnV2"]["items"]
    stubs = [entry for entry in items if "uri" not in (entry.get("data") or {})]
    assert stubs, "the fixture must keep some unexpandable entries, or this proves nothing"

    assert discovered_on_playlist_ids(overview) == PITCHABLE_IDS


def test_no_playlist_from_the_featuring_section_comes_back(overview):
    featuring = {
        (entry.get("data") or {})["uri"].removeprefix("spotify:playlist:")
        for entry in sections(overview)["featuringV2"]["items"]
    }
    assert len(featuring) == 8

    assert featuring.isdisjoint(discovered_on_playlist_ids(overview))


# --- Behaviour the real capture cannot show --------------------------------------------------


def test_a_pitchable_playlist_in_the_featuring_section_is_still_ignored():
    """The test that proves the section is ignored, not merely filtered by the editorial prefix."""
    payload = payload_with(discovered=[pid(1)], featuring=[pid(2)])

    assert discovered_on_playlist_ids(payload) == [pid(1)]


def test_ids_come_back_in_spotifys_order():
    payload = payload_with(discovered=[pid(3), pid(1), pid(2)])

    assert discovered_on_playlist_ids(payload) == [pid(3), pid(1), pid(2)]


def test_a_playlist_listed_twice_comes_back_once():
    payload = payload_with(discovered=[pid(1), pid(2), pid(1)])

    assert discovered_on_playlist_ids(payload) == [pid(1), pid(2)]


def test_every_editorial_id_is_dropped_wherever_it_sits():
    payload = payload_with(discovered=["37i9dQZF1DZ06evO3JKYkV", pid(1), "37i9dQZF1DWUrPBdYfoJvz"])

    assert discovered_on_playlist_ids(payload) == [pid(1)]


# --- Payloads that changed shape --------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [None, [], "", 7, {}, {"data": None}, {"data": {}}, {"data": {"artistUnion": None}}],
    ids=["none", "list", "string", "number", "empty", "no data", "no artist", "null artist"],
)
def test_a_payload_that_is_not_an_artist_page_yields_nothing(payload):
    assert discovered_on_playlist_ids(payload) == []


def test_an_artist_with_no_related_content_yields_nothing(overview):
    del overview["data"]["artistUnion"]["relatedContent"]

    assert discovered_on_playlist_ids(overview) == []


def test_an_artist_with_no_discovered_on_section_yields_nothing(overview):
    del sections(overview)["discoveredOnV2"]

    assert discovered_on_playlist_ids(overview) == []


@pytest.mark.parametrize(
    "section",
    [None, {}, {"items": []}, {"items": None}, {"items": {}}, {"totalCount": 100}],
    ids=["null", "empty", "no items", "null items", "items not a list", "count only"],
)
def test_an_empty_or_oddly_shaped_section_yields_nothing(overview, section):
    sections(overview)["discoveredOnV2"] = section

    assert discovered_on_playlist_ids(overview) == []


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": {"__typename": "GenericError"}},
        {"data": {"uri": None}},
        {"data": {"uri": 12}},
        {"data": {"uri": "spotify:album:4Gfnly5CzMJQqkUFfoHaP3"}},
        {"data": {"uri": "spotify:playlist:"}},
        {"data": {"uri": "spotify:playlist:tooshort"}},
        {"data": {"uri": "68JXTKfqFZEWO1DQRdVndh"}},
    ],
    ids=[
        "no data",
        "null data",
        "empty data",
        "error entry",
        "null uri",
        "uri not a string",
        "an album",
        "uri with no id",
        "id too short",
        "bare id, no uri",
    ],
)
def test_an_unusable_entry_is_skipped_and_the_rest_still_arrive(entry):
    payload = payload_with(discovered=[pid(1)])
    payload["data"]["artistUnion"]["relatedContent"]["discoveredOnV2"]["items"].insert(0, entry)

    assert discovered_on_playlist_ids(payload) == [pid(1)]


def test_an_entry_that_is_not_a_dict_is_skipped():
    payload = payload_with(discovered=[pid(1)])
    payload["data"]["artistUnion"]["relatedContent"]["discoveredOnV2"]["items"].insert(0, "nonsense")

    assert discovered_on_playlist_ids(payload) == [pid(1)]


def test_reading_the_payload_does_not_change_it(overview):
    before = copy.deepcopy(overview)

    discovered_on_playlist_ids(overview)

    assert overview == before
