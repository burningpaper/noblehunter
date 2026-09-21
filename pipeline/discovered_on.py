"""The playlists people found an artist through, read from a logged-out artist page.

A Spotify artist page carries a section called `discoveredOnV2`: the playlists Spotify's own
listening data says people discovered that artist through. The spike on 2026-09-20 is the
reason this source exists -- of 96 pitchable playlists it named, 90 had never been seen by
five days of web search. It is a different pool, not a better way to search the same one.

This module is only the reading. `pipeline.spotify` fetches the payload, and only a live test
can exercise that; everything that can be got wrong is here, in pure functions over a dict, so
it can be tested against a captured response without a browser or a network call.

Two rules that look arbitrary and are not:

- **Spotify's own editorial playlists are dropped.** Nobody behind a `37i9dQZF1...` id reads a
  pitch, so a lead there is not a lead.
- **`featuringV2` is ignored.** It sits in the same payload, one key away, and looks like the
  same kind of thing. In the spike it was 8-for-8 Spotify editorial -- every entry unpitchable.
  It is named here so nobody adds it later believing it was simply overlooked.

Nothing here raises. `queryArtistOverview` is undocumented and can change shape without notice,
and an artist page we no longer recognise should cost that one artist's contribution for a
night rather than bringing down the run.
"""

from pipeline.spotify import SPOTIFY_ID_PATTERN, SPOTIFY_OWNED_PREFIX

PLAYLIST_URI_PREFIX = "spotify:playlist:"
DISCOVERED_ON_PATH = ("data", "artistUnion", "relatedContent", "discoveredOnV2", "items")


def discovered_on_playlist_ids(payload: object) -> list[str]:
    """The pitchable playlist ids in an artist overview, in Spotify's order, without repeats.

    Spotify's order is kept because it is a ranking -- the playlists most people arrived
    through come first -- and nothing downstream would be able to recover it afterwards.
    """
    playlist_ids: list[str] = []
    seen: set[str] = set()
    for entry in _entries(payload):
        playlist_id = _playlist_id(entry)
        if playlist_id is None or playlist_id in seen or playlist_id.startswith(SPOTIFY_OWNED_PREFIX):
            continue
        seen.add(playlist_id)
        playlist_ids.append(playlist_id)
    return playlist_ids


def _entries(payload: object) -> list:
    """The `discoveredOnV2` items, or nothing at all if the payload isn't shaped like one."""
    items = _dig(payload, *DISCOVERED_ON_PATH)
    return items if isinstance(items, list) else []


def _dig(value: object, *keys: str) -> object:
    """Walk nested keys, giving up quietly the moment the shape stops matching."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _playlist_id(entry: object) -> str | None:
    """`{"data": {"uri": "spotify:playlist:<id>"}}` -> `<id>`, or None if it is anything else.

    Real captures are full of entries Spotify declined to expand -- `{"data": {"__typename":
    "GenericError"}}`, with no uri at all -- so "anything else" is the common case, not the
    exception. The id shape is checked too: a truncated uri should yield nothing rather than a
    plausible-looking id that no playlist will ever answer to.
    """
    uri = _dig(entry, "data", "uri")
    if not isinstance(uri, str) or not uri.startswith(PLAYLIST_URI_PREFIX):
        return None
    playlist_id = uri.removeprefix(PLAYLIST_URI_PREFIX)
    return playlist_id if SPOTIFY_ID_PATTERN.match(playlist_id) else None
