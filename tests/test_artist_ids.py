"""Resolving a reference artist's name to a Spotify artist id, and refusing to guess.

Nothing here touches the network. The fixture is a real capture of `searchArtists` for
"Aphex Twin", and it happens to be the best argument in the repo for how careful this has
to be: its eight hits include a *second* artist page also called "Aphex Twin" with a
different id, and an act called "aphex twink" one character away from the real name. Any
rule loose enough to forgive a typo would take one of those, and a wrong id is stored --
it would feed another act's playlists into the profile every night until someone noticed.

So the tests that matter most are the ones asserting `None`: a near-miss, a plural, an
accent dropped. Each of those costs one artist's contribution and says so out loud, which
is the cheap failure. There is deliberately no test admitting a fuzzy match, because the
fixture shows the fuzzy neighbourhood of "Aphex Twin" is occupied by somebody else.
"""

import copy
import json
import unicodedata
from pathlib import Path

import pytest
from sqlalchemy import select

from core.models import ReferenceArtist
from core.profiles import create_profile
from pipeline.artist_ids import (
    artist_id_from_search,
    is_confident_match,
    resolve_artist_id,
    resolve_missing_artist_ids,
    top_artist,
)
from pipeline.spotify import SpotifyFetchError
from tests.factories import default_artist_id

FIXTURES = Path(__file__).parent / "fixtures" / "spotify"
APHEX_TWIN = "6kBDZFXuLrZgHnvmPu9NsG"  # the top hit, and the artist the overview fixture came from
APHEX_TWINK = "5Ea01KqLoX4lMgTo8z3J3g"  # a different act, one character away, fourth in the fixture
OLAFUR = "7gV3DW73k1V0sIz4Kkp4dz"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def search() -> dict:
    return load("searchArtists_aphex_twin.json")


def aid(number: int) -> str:
    """A valid-looking 22-character Spotify artist id."""
    return f"artistid{number:0>14}"


def hit(name: str, spotify_id: str) -> dict:
    return {
        "__typename": "ArtistResponseWrapper",
        "data": {"__typename": "Artist", "profile": {"name": name}, "uri": f"spotify:artist:{spotify_id}"},
    }


def payload_with(*hits: dict) -> dict:
    """A payload nested the way Spotify nests one, carrying exactly these hits in order."""
    return {"data": {"searchV2": {"artists": {"totalCount": len(hits), "items": list(hits)}}}}


def items(payload: dict) -> list:
    return payload["data"]["searchV2"]["artists"]["items"]


# --- The real capture -------------------------------------------------------------------------


def test_the_top_hit_of_a_real_search_is_the_artist_we_asked_for(search):
    assert artist_id_from_search(search, "Aphex Twin") == APHEX_TWIN


def test_only_the_top_hit_is_considered_even_when_a_later_hit_wears_the_same_name(search):
    """The reason resolution never scans past position one.

    Two entries in this capture are both called "Aphex Twin" and carry different ids. Only
    one of them is the artist; nothing in the payload says which, except the order. A search
    for the first exact match further down the list would be a coin toss dressed as a check.
    """
    named_aphex_twin = [
        entry["data"]["uri"] for entry in items(search) if entry["data"]["profile"]["name"] == "Aphex Twin"
    ]
    assert len(named_aphex_twin) > 1, "the fixture must keep its duplicate name, or this proves nothing"

    assert artist_id_from_search(search, "Aphex Twin") == APHEX_TWIN


def test_asking_for_a_different_act_gets_nothing_rather_than_the_top_hit(search):
    assert artist_id_from_search(search, "Boards of Canada") is None


def test_a_name_one_character_out_is_a_different_act_and_is_refused(search):
    """`aphex twink` is in this capture, and is somebody else. So no edit-distance rule."""
    names = [entry["data"]["profile"]["name"] for entry in items(search)]
    assert "aphex twink" in names, "the fixture must keep its near-miss, or this proves nothing"

    assert artist_id_from_search(payload_with(hit("aphex twink", APHEX_TWINK)), "Aphex Twin") is None


def test_a_plural_of_the_asked_name_is_refused(search):
    # "Aphex Twins" and "aphex twink" are the same distance from "Aphex Twin". A rule that
    # forgives the first cannot refuse the second, and the second is a wrong id.
    assert artist_id_from_search(search, "Aphex Twins") is None


def test_reading_the_payload_does_not_change_it(search):
    before = copy.deepcopy(search)

    artist_id_from_search(search, "Aphex Twin")

    assert search == before


# --- What counts as the same name -------------------------------------------------------------


@pytest.mark.parametrize(
    "asked",
    ["Aphex Twin", "aphex twin", "APHEX TWIN", "  Aphex   Twin  ", "Aphex\tTwin"],
    ids=["as stored", "lower", "upper", "extra spaces", "a tab"],
)
def test_case_and_spacing_are_not_differences(search, asked):
    assert artist_id_from_search(search, asked) == APHEX_TWIN


def test_an_accented_name_resolves():
    payload = payload_with(hit("Ólafur Arnalds", OLAFUR))

    assert artist_id_from_search(payload, "Ólafur Arnalds") == OLAFUR


def decomposed(value: str) -> str:
    """The same name typed as "o" plus a combining accent, which is what some keyboards send."""
    return unicodedata.normalize("NFD", value)


OLAFUR_ARNALDS = "\u00d3lafur Arnalds"


@pytest.mark.parametrize(
    ("asked", "found"),
    [
        (decomposed(OLAFUR_ARNALDS), OLAFUR_ARNALDS),
        (OLAFUR_ARNALDS, decomposed(OLAFUR_ARNALDS)),
        (decomposed(OLAFUR_ARNALDS), decomposed(OLAFUR_ARNALDS)),
    ],
    ids=["asked decomposed", "found decomposed", "both decomposed"],
)
def test_the_same_accent_written_two_ways_is_still_the_same_name(asked, found):
    """Two encodings of one letter are a difference in representation, not in identity."""
    # Guard: were these the same string, the test would pass without proving anything.
    assert decomposed(OLAFUR_ARNALDS) != OLAFUR_ARNALDS

    assert artist_id_from_search(payload_with(hit(found, OLAFUR)), asked) == OLAFUR


def test_a_name_with_its_accents_dropped_is_refused():
    """Deliberate, and the cheaper of two mistakes.

    Folding "ó" to "o" would also fold "Ø" to "O" and "Sigur Rós" to "Sigur Ros" -- and some
    of those pairs are different acts. Refusing costs this artist one night and a report line
    saying exactly which name Spotify offered, which a human fixes in the profile editor once.
    """
    assert artist_id_from_search(payload_with(hit("Ólafur Arnalds", OLAFUR)), "Olafur Arnalds") is None


def test_a_missing_leading_article_is_refused():
    """ "A Winged Victory for the Sullen" is not "Winged Victory for the Sullen", to us.

    Stripping articles would make "The Field" and "Field" the same name, and they need not
    be the same band. The report names the top hit, so the fix is visible and one edit long.
    """
    payload = payload_with(hit("A Winged Victory for the Sullen", aid(1)))

    assert artist_id_from_search(payload, "Winged Victory for the Sullen") is None


@pytest.mark.parametrize(
    ("wanted", "found", "expected"),
    [
        ("Aphex Twin", "Aphex Twin", True),
        ("aphex twin", "APHEX TWIN", True),
        ("Aphex Twin", "Aphex Twins", False),
        ("Aphex Twin", "aphex twink", False),
        ("Aphex Twin", "AFX", False),
        ("Aphex Twin", "", False),
        ("", "Aphex Twin", False),
        ("", "", False),
        ("   ", "   ", False),
    ],
    ids=[
        "identical",
        "case only",
        "a plural",
        "one letter",
        "an alias",
        "found is empty",
        "wanted is empty",
        "both empty",
        "both blank",
    ],
)
def test_what_counts_as_a_confident_match(wanted, found, expected):
    # Two empty names must not match each other: a blank display name would otherwise take
    # the id of any hit Spotify declined to name.
    assert is_confident_match(wanted, found) is expected


def test_an_artists_stored_normalised_name_is_a_usable_question():
    """Resolution asks with the same normalised form the row already carries, not a new one."""
    artist = ReferenceArtist(display_name="  APHEX   twin ")

    assert artist.normalized_name == "aphex twin"
    assert is_confident_match(artist.normalized_name, "Aphex Twin") is True


# --- Payloads that changed shape --------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "",
        7,
        {},
        {"data": None},
        {"data": {}},
        {"data": {"searchV2": None}},
        {"data": {"searchV2": {}}},
        {"data": {"searchV2": {"artists": None}}},
        {"data": {"searchV2": {"artists": {}}}},
        {"data": {"searchV2": {"artists": {"items": None}}}},
        {"data": {"searchV2": {"artists": {"items": {}}}}},
        {"data": {"searchV2": {"artists": {"totalCount": 0, "items": []}}}},
        {"errors": [{"message": "PersistedQueryNotFound"}], "data": None},
    ],
    ids=[
        "none",
        "list",
        "string",
        "number",
        "empty",
        "no data",
        "no search",
        "null search",
        "no artists",
        "null artists",
        "no items",
        "null items",
        "items not a list",
        "no hits",
        "graphql errors",
    ],
)
def test_a_payload_that_is_not_a_search_result_yields_nothing(payload):
    assert artist_id_from_search(payload, "Aphex Twin") is None
    assert top_artist(payload) is None


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": {"__typename": "GenericError"}},
        {"data": {"uri": f"spotify:artist:{APHEX_TWIN}"}},
        {"data": {"profile": None, "uri": f"spotify:artist:{APHEX_TWIN}"}},
        {"data": {"profile": {}, "uri": f"spotify:artist:{APHEX_TWIN}"}},
        {"data": {"profile": {"name": None}, "uri": f"spotify:artist:{APHEX_TWIN}"}},
        {"data": {"profile": {"name": 12}, "uri": f"spotify:artist:{APHEX_TWIN}"}},
        {"data": {"profile": {"name": "Aphex Twin"}}},
        {"data": {"profile": {"name": "Aphex Twin"}, "uri": None}},
        {"data": {"profile": {"name": "Aphex Twin"}, "uri": "spotify:album:4Gfnly5CzMJQqkUFfoHaP3"}},
        {"data": {"profile": {"name": "Aphex Twin"}, "uri": "spotify:artist:"}},
        {"data": {"profile": {"name": "Aphex Twin"}, "uri": "spotify:artist:tooshort"}},
        {"data": {"profile": {"name": "Aphex Twin"}, "uri": APHEX_TWIN}},
        "nonsense",
        None,
    ],
    ids=[
        "no data",
        "null data",
        "empty data",
        "error entry",
        "no profile",
        "null profile",
        "empty profile",
        "null name",
        "name not a string",
        "no uri",
        "null uri",
        "an album",
        "uri with no id",
        "id too short",
        "bare id, no uri",
        "not a dict",
        "null entry",
    ],
)
def test_an_unusable_top_hit_yields_nothing_rather_than_raising(entry):
    # Never the second hit either: an unreadable top hit is a reason to stop, not to shop.
    payload = payload_with(entry, hit("Aphex Twin", APHEX_TWIN))

    assert artist_id_from_search(payload, "Aphex Twin") is None


def test_an_empty_name_never_matches_anything(search):
    assert artist_id_from_search(search, "") is None
    assert artist_id_from_search(search, "   ") is None


def test_the_top_hit_is_read_without_judging_it(search):
    """`top_artist` reports what Spotify said; the judging is a separate, testable step."""
    found = top_artist(search)

    assert (found.spotify_id, found.name) == (APHEX_TWIN, "Aphex Twin")


# --- Resolution, against the database ---------------------------------------------------------


class FakeSearch:
    """Answers with a canned payload per name, and remembers every name it was asked for."""

    def __init__(self, payloads: dict[str, dict] | None = None, errors: dict[str, Exception] | None = None):
        self.payloads = payloads or {}
        self.errors = errors or {}
        self.asked: list[str] = []

    def fetch_artist_search(self, name: str) -> object:
        self.asked.append(name)
        if name in self.errors:
            raise self.errors[name]
        return self.payloads.get(name, payload_with())


@pytest.fixture
def profile(session):
    return create_profile(session, default_artist_id(session), "Synman")


def add_artist(session, profile, display_name: str, spotify_artist_id: str | None = None):
    artist = ReferenceArtist(display_name=display_name, spotify_artist_id=spotify_artist_id)
    profile.reference_artists.append(artist)
    session.flush()
    return artist


def stored_id(session, artist_id: int) -> str | None:
    return session.scalar(select(ReferenceArtist.spotify_artist_id).where(ReferenceArtist.id == artist_id))


class TestResolvingOneArtist:
    def test_a_confident_match_is_stored(self, session, profile):
        artist = add_artist(session, profile, "Aphex Twin")
        client = FakeSearch({"Aphex Twin": payload_with(hit("Aphex Twin", APHEX_TWIN))})

        outcome = resolve_artist_id(session, artist, client)

        assert outcome.spotify_id == APHEX_TWIN
        assert outcome.reason is None
        assert stored_id(session, artist.id) == APHEX_TWIN

    def test_the_display_name_is_what_gets_searched_for(self, session, profile):
        artist = add_artist(session, profile, "Ólafur Arnalds")
        client = FakeSearch({"Ólafur Arnalds": payload_with(hit("Ólafur Arnalds", OLAFUR))})

        resolve_artist_id(session, artist, client)

        assert client.asked == ["Ólafur Arnalds"]
        assert stored_id(session, artist.id) == OLAFUR

    def test_a_top_hit_that_is_another_act_stores_nothing_and_names_it(self, session, profile):
        artist = add_artist(session, profile, "Aphex Twin")
        client = FakeSearch({"Aphex Twin": payload_with(hit("aphex twink", APHEX_TWINK))})

        outcome = resolve_artist_id(session, artist, client)

        assert outcome.spotify_id is None
        assert "aphex twink" in outcome.reason
        assert stored_id(session, artist.id) is None

    def test_a_search_that_found_nobody_stores_nothing_and_says_so(self, session, profile):
        artist = add_artist(session, profile, "Nobody At All")
        client = FakeSearch()

        outcome = resolve_artist_id(session, artist, client)

        assert outcome.spotify_id is None
        assert outcome.reason
        assert stored_id(session, artist.id) is None

    @pytest.mark.parametrize("kind", ["timeout", "http", "parse", "blocked", "not_found"])
    def test_a_failed_fetch_leaves_the_column_null_and_is_reported(self, session, profile, kind):
        artist = add_artist(session, profile, "Aphex Twin")
        client = FakeSearch(errors={"Aphex Twin": SpotifyFetchError(kind, "Artist search: went wrong")})

        outcome = resolve_artist_id(session, artist, client)

        assert outcome.spotify_id is None
        assert "went wrong" in outcome.reason
        assert stored_id(session, artist.id) is None

    def test_an_unexpected_error_is_still_that_artists_problem_alone(self, session, profile):
        artist = add_artist(session, profile, "Aphex Twin")
        client = FakeSearch(errors={"Aphex Twin": ValueError("the browser is on fire")})

        outcome = resolve_artist_id(session, artist, client)

        assert outcome.spotify_id is None
        assert "browser is on fire" in outcome.reason


class TestResolvingAProfilesArtists:
    def test_only_the_artists_without_an_id_are_looked_up(self, session, profile):
        known = add_artist(session, profile, "Boards of Canada", spotify_artist_id=aid(9))
        unknown = add_artist(session, profile, "Aphex Twin")
        client = FakeSearch({"Aphex Twin": payload_with(hit("Aphex Twin", APHEX_TWIN))})

        summary = resolve_missing_artist_ids(session, profile.reference_artists, client)

        assert client.asked == ["Aphex Twin"]
        assert (summary.resolved, summary.already_known, summary.failures) == (1, 1, ())
        assert stored_id(session, known.id) == aid(9)
        assert stored_id(session, unknown.id) == APHEX_TWIN

    def test_a_second_run_over_the_same_artists_fetches_nothing(self, session, profile):
        add_artist(session, profile, "Aphex Twin")
        client = FakeSearch({"Aphex Twin": payload_with(hit("Aphex Twin", APHEX_TWIN))})
        resolve_missing_artist_ids(session, profile.reference_artists, client)

        summary = resolve_missing_artist_ids(session, profile.reference_artists, client)

        assert client.asked == ["Aphex Twin"]  # the first run's only fetch
        assert (summary.resolved, summary.already_known) == (0, 1)

    def test_one_artist_failing_costs_only_that_artist(self, session, profile):
        first = add_artist(session, profile, "Aphex Twin")
        broken = add_artist(session, profile, "Boards of Canada")
        last = add_artist(session, profile, "Autechre")
        client = FakeSearch(
            payloads={
                "Aphex Twin": payload_with(hit("Aphex Twin", APHEX_TWIN)),
                "Autechre": payload_with(hit("Autechre", aid(3))),
            },
            errors={"Boards of Canada": SpotifyFetchError("timeout", "Artist search: timed out")},
        )

        summary = resolve_missing_artist_ids(session, profile.reference_artists, client)

        assert summary.resolved == 2
        assert summary.failed_names == ("Boards of Canada",)
        assert stored_id(session, first.id) == APHEX_TWIN
        assert stored_id(session, broken.id) is None
        assert stored_id(session, last.id) == aid(3)

    def test_the_failures_say_which_name_and_why(self, session, profile):
        add_artist(session, profile, "Winged Victory for the Sullen")
        client = FakeSearch(
            {"Winged Victory for the Sullen": payload_with(hit("A Winged Victory for the Sullen", aid(4)))}
        )

        summary = resolve_missing_artist_ids(session, profile.reference_artists, client)

        failure = summary.failures[0]
        assert failure.display_name == "Winged Victory for the Sullen"
        assert "A Winged Victory for the Sullen" in failure.reason
        assert summary.resolved == 0

    def test_a_profile_with_no_reference_artists_does_nothing_quietly(self, session):
        client = FakeSearch()

        summary = resolve_missing_artist_ids(session, [], client)

        assert (summary.resolved, summary.already_known, summary.failures) == (0, 0, ())
        assert summary.attempted == 0
        assert client.asked == []

    def test_the_summary_adds_up(self, session, profile):
        add_artist(session, profile, "Aphex Twin")
        add_artist(session, profile, "Boards of Canada", spotify_artist_id=aid(9))
        add_artist(session, profile, "Nobody At All")
        client = FakeSearch({"Aphex Twin": payload_with(hit("Aphex Twin", APHEX_TWIN))})

        summary = resolve_missing_artist_ids(session, profile.reference_artists, client)

        assert (summary.resolved, summary.already_known, len(summary.failures)) == (1, 1, 1)
        assert summary.attempted == 2  # the known one was never attempted
