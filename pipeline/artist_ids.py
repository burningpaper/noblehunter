"""Turning a reference artist's name into a Spotify artist id -- once, and never by guessing.

"Discovered on" needs a Spotify artist id. Noble Hunter stores artist *names*, because that is
what a person types into a profile. `reference_artists.spotify_artist_id` has existed since
migration 0001, nullable, and nothing has ever filled it. This is the missing link.

(The name is unavoidably ambiguous in this codebase: an "artist" in `core/access.py` is one of
Noble Hunter's own clients. Everything here is about a *Spotify* artist id, stored on a
profile's reference artists -- the acts a playlist should sound like.)

**A wrong id is far worse than no id**, and the asymmetry is the whole design. A missing id
costs one artist's contribution on one night, and says so in the run report. A wrong id is
*stored*: every night after, for ever, another act's "Discovered on" playlists are pitched into
this profile, and nothing in the pipeline can tell. So the rule is exact -- after case-folding,
Unicode normalisation and whitespace collapsing, the same comparison form the row already
carries in `normalized_name` -- and anything short of that records nothing and reports it.

Why not something more forgiving? The committed search fixture answers it. Asked for "Aphex
Twin", Spotify returns a second artist page also called "Aphex Twin" with a different id, and
an act called "aphex twink". A rule loose enough to forgive "Aphex Twins" cannot tell that
apart from "aphex twink", and one of those is somebody else. Accents are the same argument:
folding "ó" to "o" would also fold "Ø" to "O", and those are not always the same act. A
rejection is visible, one-off and fixable in the profile editor; a wrong id is none of those.

Two concerns live here, kept apart because one is pure and one talks to a browser. The parsing
is functions over a dict, testable against a captured payload. The resolution needs a session
and a client, and treats every failure as that artist's alone: a name that matches nothing, a
fetch that times out, a payload that changed shape -- each costs one artist and never the run.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from core.models import ReferenceArtist
from core.text import normalize_text
from pipeline.spotify import SPOTIFY_ID_PATTERN

logger = logging.getLogger(__name__)

ARTIST_URI_PREFIX = "spotify:artist:"
SEARCH_ITEMS_PATH = ("data", "searchV2", "artists", "items")

NO_HIT = "Spotify's search returned no usable artist"


class ArtistSearch(Protocol):
    """What resolution needs from `SpotifyWebClient`: a name in, a `searchArtists` payload out."""

    def fetch_artist_search(self, name: str) -> object: ...


@dataclass(frozen=True)
class ArtistHit:
    """What Spotify put at the top of its results, before anyone decided whether to believe it."""

    spotify_id: str
    name: str


@dataclass(frozen=True)
class ArtistIdOutcome:
    """One artist's lookup. `reason` is filled exactly when `spotify_id` is not."""

    display_name: str
    spotify_id: str | None
    reason: str | None


@dataclass(frozen=True)
class ArtistIdSummary:
    resolved: int
    already_known: int  # skipped without a fetch: an id, once stored, is never looked up again
    failures: tuple[ArtistIdOutcome, ...]

    @property
    def failed_names(self) -> tuple[str, ...]:
        return tuple(failure.display_name for failure in self.failures)

    @property
    def attempted(self) -> int:
        return self.resolved + len(self.failures)


# --- Parsing ----------------------------------------------------------------------------------


def artist_id_from_search(payload: object, wanted_name: str) -> str | None:
    """The top hit's artist id, but only when its name is a confident match for `wanted_name`."""
    found = top_artist(payload)
    if found is None or not is_confident_match(wanted_name, found.name):
        return None
    return found.spotify_id


def top_artist(payload: object) -> ArtistHit | None:
    """The first hit Spotify returned, or None if there isn't a usable one.

    Only the first. The fixture holds two artists both called "Aphex Twin", with different
    ids, and nothing in the payload says which is the real one except the order -- so hunting
    further down the list for a name that matches would be a coin toss wearing a check's
    clothes. An unreadable top hit is a reason to stop, not to shop.
    """
    entries = _dig(payload, *SEARCH_ITEMS_PATH)
    if not isinstance(entries, list) or not entries:
        return None
    return _hit(entries[0])


def is_confident_match(wanted: str, found: str) -> bool:
    """Are these two the same name? Only if they are the same after normalising.

    `normalize_text` is the project's one notion of sameness -- NFKC, case-folded, single
    spaced -- and it is what `reference_artists.normalized_name` already holds, so this asks
    the same question the database answered when the name was typed. It forgives how a name
    is *written* (case, spacing, a combining accent instead of a precomposed one) and nothing
    about what it *says*. Two empty names are not a match: a blank display name would
    otherwise take the id of any hit Spotify declined to name.
    """
    wanted_key = normalize_text(wanted)
    return bool(wanted_key) and wanted_key == normalize_text(found)


def _hit(entry: object) -> ArtistHit | None:
    """`{"data": {"uri": "spotify:artist:<id>", "profile": {"name": ...}}}` -> a hit, or None.

    Both halves are required. An id with no name cannot be checked against anything, and an
    unchecked id is exactly the thing this module exists to refuse.
    """
    uri = _dig(entry, "data", "uri")
    name = _dig(entry, "data", "profile", "name")
    if not isinstance(uri, str) or not uri.startswith(ARTIST_URI_PREFIX) or not isinstance(name, str):
        return None
    spotify_id = uri.removeprefix(ARTIST_URI_PREFIX)
    return ArtistHit(spotify_id, name) if SPOTIFY_ID_PATTERN.match(spotify_id) else None


def _dig(value: object, *keys: str) -> object:
    """Walk nested keys, giving up quietly the moment the shape stops matching."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


# --- Resolution -------------------------------------------------------------------------------


def resolve_missing_artist_ids(
    session: Session, artists: Iterable[ReferenceArtist], client: ArtistSearch
) -> ArtistIdSummary:
    """Look up every reference artist that hasn't got an id yet, and store what is certain.

    Resolution happens once per artist, ever -- not nightly. An artist already carrying an id
    is skipped before any fetch, which is why the recurring cost of this feature is one page
    load per *new* reference artist rather than one per artist per night.
    """
    resolved = 0
    already_known = 0
    failures: list[ArtistIdOutcome] = []
    for artist in artists:
        if artist.spotify_artist_id:
            already_known += 1
            continue
        outcome = resolve_artist_id(session, artist, client)
        if outcome.spotify_id is None:
            failures.append(outcome)
        else:
            resolved += 1
    return ArtistIdSummary(resolved=resolved, already_known=already_known, failures=tuple(failures))


def resolve_artist_id(session: Session, artist: ReferenceArtist, client: ArtistSearch) -> ArtistIdOutcome:
    """Search Spotify for one artist's name and store the id, if and only if it is certain.

    Nothing raises. The row is left untouched on any doubt, and the outcome carries the reason
    so the run report can say which names went unresolved and what Spotify offered instead --
    which is usually enough for a human to fix the spelling in the profile editor.
    """
    try:
        found = top_artist(client.fetch_artist_search(artist.display_name))
    except Exception as error:  # a broken lookup is this artist's problem and no one else's
        return _failed(artist, f"the lookup failed ({type(error).__name__}: {error})")

    if found is None:
        return _failed(artist, NO_HIT)
    if not is_confident_match(artist.normalized_name, found.name):
        return _failed(artist, f"Spotify's top hit was {found.name!r}, which is not a confident match")

    artist.spotify_artist_id = found.spotify_id
    # Flushed, not committed: the caller owns the transaction, as it does for every other step.
    session.flush()
    return ArtistIdOutcome(display_name=artist.display_name, spotify_id=found.spotify_id, reason=None)


def _failed(artist: ReferenceArtist, reason: str) -> ArtistIdOutcome:
    logger.warning("No Spotify id stored for %r: %s", artist.display_name, reason)
    return ArtistIdOutcome(display_name=artist.display_name, spotify_id=None, reason=reason)
