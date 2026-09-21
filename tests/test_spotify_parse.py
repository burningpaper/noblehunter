import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.spotify import (
    ProfilePlaylist,
    RateLimiter,
    SpotifyFetchError,
    SpotifyWebClient,
    _artist_union_from_payload,
    _is_artist_overview_request,
    _json_body,
    _playlist_v2_from_payload,
    _raise_for_status,
    call_with_retries,
    parse_playlist,
    parse_profile_playlists,
    plan_page_offsets,
)

FIXTURES = Path(__file__).parent / "fixtures" / "spotify"
PLAYLIST_ID = "3mA6qe4mVO0vDRH0cZM1hJ"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def first_page() -> dict:
    return load(f"fetchPlaylist_{PLAYLIST_ID}.json")["data"]["playlistV2"]


@pytest.fixture
def contents_page_items() -> list[dict]:
    payload = load(f"fetchPlaylistContents_{PLAYLIST_ID}_offset44.json")
    return payload["data"]["playlistV2"]["content"]["items"]


def test_parse_playlist_reads_metadata_from_real_response(first_page):
    playlist = parse_playlist(PLAYLIST_ID, first_page)

    assert playlist.spotify_id == PLAYLIST_ID
    assert playlist.name == "IDM /  Braindance / Techno"
    assert playlist.owner_id == "1251802306"
    assert playlist.owner_name == "Mimi Taylor"
    assert playlist.followers == 44
    assert playlist.total_tracks == 127
    assert len(playlist.tracks) == 25
    assert playlist.partial is False


def test_parse_playlist_reads_tracks_and_artists(first_page):
    tracks = parse_playlist(PLAYLIST_ID, first_page).tracks

    remix = tracks[7]
    assert remix.uid == "51339736c9c936b2"
    assert remix.name == "Katzenfutter - Vamos Art Remix"
    assert remix.artists == ("Melokind", "Vamos Art")


def test_parse_playlist_reads_added_dates_as_aware_utc(first_page):
    tracks = parse_playlist(PLAYLIST_ID, first_page).tracks

    assert tracks[0].added_at == datetime(2019, 6, 7, 6, 12, 29, 850000, tzinfo=UTC)
    assert tracks[2].added_at == datetime(2025, 3, 3, 7, 49, 14, tzinfo=UTC)
    assert all(track.added_at.utcoffset().total_seconds() == 0 for track in tracks)


def test_added_date_without_a_zone_is_read_as_utc(first_page):
    first_page["content"]["items"][0]["addedAt"]["isoString"] = "2024-01-02T03:04:05"

    track = parse_playlist(PLAYLIST_ID, first_page).tracks[0]

    assert track.added_at == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert track.added_at.tzinfo is not None


def with_items(playlist_v2: dict, items: list[dict]) -> dict:
    changed = copy.deepcopy(playlist_v2)
    changed["content"]["items"] = items
    return changed


def test_item_without_added_date_has_none(first_page):
    item = copy.deepcopy(first_page["content"]["items"][0])
    del item["addedAt"]

    track = parse_playlist(PLAYLIST_ID, with_items(first_page, [item])).tracks[0]

    assert track.added_at is None
    assert track.artists == ("Plaid",)


@pytest.mark.parametrize("typename", ["Episode", "NotFound", "RestrictedContent"])
def test_non_track_items_count_but_have_no_artists(first_page, typename):
    item = copy.deepcopy(first_page["content"]["items"][0])
    item["itemV2"]["data"]["__typename"] = typename

    tracks = parse_playlist(PLAYLIST_ID, with_items(first_page, [item])).tracks

    assert len(tracks) == 1
    assert tracks[0].artists == ()


def test_unavailable_item_without_name_or_artists_does_not_crash(first_page):
    item = copy.deepcopy(first_page["content"]["items"][0])
    item["itemV2"]["data"] = {"__typename": "NotFound"}

    track = parse_playlist(PLAYLIST_ID, with_items(first_page, [item])).tracks[0]

    assert track.name is None
    assert track.artists == ()


@pytest.mark.parametrize("broken", [{}, {"itemV2": None}, {"itemV2": {}}, {"itemV2": {"data": None}}])
def test_item_missing_item_data_does_not_crash(first_page, broken):
    item = copy.deepcopy(first_page["content"]["items"][0])
    del item["itemV2"]
    item.update(broken)

    track = parse_playlist(PLAYLIST_ID, with_items(first_page, [item])).tracks[0]

    assert track.uid == "9818895d7e2b308d"
    assert (track.name, track.artists) == (None, ())


def test_description_keeps_html_and_derives_plain_text(first_page):
    first_page["description"] = '<a href="https://x">link</a> &amp;   more'

    playlist = parse_playlist(PLAYLIST_ID, first_page)

    assert playlist.description_html == '<a href="https://x">link</a> &amp;   more'
    assert playlist.description_text == "link & more"


def test_description_entities_that_look_like_tags_survive(first_page):
    first_page["description"] = "Beats &lt;3 Psychill &#x2F; Chillout &#x27;90s"

    assert parse_playlist(PLAYLIST_ID, first_page).description_text == "Beats <3 Psychill / Chillout '90s"


@pytest.mark.parametrize("missing", [None, ""])
def test_missing_description_is_empty(first_page, missing):
    first_page["description"] = missing

    playlist = parse_playlist(PLAYLIST_ID, first_page)

    assert (playlist.description_html, playlist.description_text) == ("", "")


def test_absent_description_key_is_empty(first_page):
    del first_page["description"]

    playlist = parse_playlist(PLAYLIST_ID, first_page)

    assert (playlist.description_html, playlist.description_text) == ("", "")


def test_cover_image_url_comes_from_first_image_source(first_page):
    playlist = parse_playlist(PLAYLIST_ID, first_page)

    assert playlist.cover_image_url == (
        "https://image-cdn-fa.spotifycdn.com/image/ab67706c0000da84b3a415c5f36355218b82fd3b"
    )


@pytest.mark.parametrize("images", [None, {}, {"items": []}, {"items": [{"sources": []}]}])
def test_cover_image_url_is_none_when_absent(first_page, images):
    first_page["images"] = images

    assert parse_playlist(PLAYLIST_ID, first_page).cover_image_url is None


def test_cover_image_url_is_none_when_images_key_missing(first_page):
    del first_page["images"]

    assert parse_playlist(PLAYLIST_ID, first_page).cover_image_url is None


def test_extra_items_are_appended_after_first_page(first_page, contents_page_items):
    playlist = parse_playlist(PLAYLIST_ID, first_page, contents_page_items)

    assert len(playlist.tracks) == 35
    assert playlist.tracks[25].uid == "25b1caef17977204"
    assert playlist.tracks[25].name == "Matin Lunaire"


def test_duplicate_uids_across_pages_appear_once(first_page, contents_page_items):
    overlapping = [first_page["content"]["items"][-1], *contents_page_items, contents_page_items[0]]

    playlist = parse_playlist(PLAYLIST_ID, first_page, overlapping)

    uids = [track.uid for track in playlist.tracks]
    assert len(uids) == len(set(uids)) == 35


@pytest.mark.parametrize("partial", [True, False])
def test_partial_flag_passes_through(first_page, partial):
    assert parse_playlist(PLAYLIST_ID, first_page, partial=partial).partial is partial


@pytest.mark.parametrize(
    "breakage",
    [
        lambda playlist: playlist.pop("content"),
        lambda playlist: playlist.pop("name"),
        lambda playlist: playlist["content"].pop("totalCount"),
        lambda playlist: playlist["content"].pop("items"),
        lambda playlist: playlist["content"]["items"][3].pop("uid"),
        lambda playlist: playlist["content"]["items"][3]["addedAt"].update(isoString="last tuesday"),
    ],
    ids=["no content", "no name", "no total", "no items", "item without uid", "bad date"],
)
def test_malformed_payload_raises_parse_error_without_dumping_it(first_page, breakage):
    breakage(first_page)

    with pytest.raises(SpotifyFetchError) as raised:
        parse_playlist(PLAYLIST_ID, first_page)

    assert raised.value.kind == "parse"
    assert PLAYLIST_ID in str(raised.value)
    assert "Mimi Taylor" not in str(raised.value)
    assert len(str(raised.value)) < 200


def test_malformed_extra_item_raises_parse_error(first_page, contents_page_items):
    broken = copy.deepcopy(contents_page_items[0])
    del broken["uid"]

    with pytest.raises(SpotifyFetchError) as raised:
        parse_playlist(PLAYLIST_ID, first_page, [broken])

    assert raised.value.kind == "parse"


# --- Curator profiles -------------------------------------------------------------------------


@pytest.fixture
def owned_playlists() -> list[dict]:
    return load("profile_playlists_1251802306.json")["public_playlists"]


@pytest.fixture
def mixed_playlists() -> list[dict]:
    return load("profile_playlists_arsendis_sample.json")["public_playlists"]


def test_parse_profile_playlists_reads_real_page(owned_playlists):
    playlists = parse_profile_playlists([owned_playlists])

    assert len(playlists) == 17
    assert playlists[0] == ProfilePlaylist(
        spotify_id="42QY1W9yRKf9kVp9gOxaT8",
        name="Spacesynth / Retrowave / Italo Disco / 80s",
        owner_id="1251802306",
        followers=1836,
    )


def test_profile_playlists_keep_other_owners_and_missing_followers(mixed_playlists):
    playlists = {playlist.spotify_id: playlist for playlist in parse_profile_playlists([mixed_playlists])}

    assert playlists["1g2ZugTpjkcwgS8uHFv0qk"].followers is None
    assert playlists["1g2ZugTpjkcwgS8uHFv0qk"].owner_id == "arsendis"
    assert playlists["7lYRXe9BOjDfaLtaSRoHRf"].owner_id == "julia.kaczmarek-04"
    assert playlists["37i9dQZEVXcFvGfl9rSbvh"].owner_id == "spotify"


def test_profile_playlists_are_deduplicated_across_overlapping_pages(mixed_playlists):
    second_page = copy.deepcopy(mixed_playlists[6:])
    second_page[0]["name"] = "renamed between pages"

    playlists = parse_profile_playlists([mixed_playlists[:8], second_page, []])

    assert [playlist.spotify_id for playlist in playlists] == [
        item["uri"].removeprefix("spotify:playlist:") for item in mixed_playlists
    ]
    assert playlists[6].name == mixed_playlists[6]["name"]


def test_profile_item_without_owner_uri_has_no_owner(owned_playlists):
    del owned_playlists[0]["owner_uri"]

    assert parse_profile_playlists([owned_playlists])[0].owner_id is None


def test_no_pages_means_no_playlists():
    assert parse_profile_playlists([]) == []


@pytest.mark.parametrize("uri", [None, "", "spotify:album:42QY1W9yRKf9kVp9gOxaT8", "spotify:playlist:"])
def test_profile_item_without_playlist_uri_raises_parse_error(owned_playlists, uri):
    owned_playlists[0]["uri"] = uri

    with pytest.raises(SpotifyFetchError) as raised:
        parse_profile_playlists([owned_playlists])

    assert raised.value.kind == "parse"


# --- Politeness -------------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_first_wait_never_sleeps():
    clock = FakeClock()

    RateLimiter(3.0, clock=clock, sleep=clock.sleep).wait()

    assert clock.sleeps == []


def test_rate_limiter_sleeps_only_the_remaining_interval():
    clock = FakeClock()
    limiter = RateLimiter(3.0, clock=clock, sleep=clock.sleep)

    limiter.wait()
    clock.now += 1.0
    limiter.wait()

    assert clock.sleeps == [pytest.approx(2.0)]


def test_rate_limiter_does_not_sleep_when_interval_already_passed():
    clock = FakeClock()
    limiter = RateLimiter(3.0, clock=clock, sleep=clock.sleep)

    limiter.wait()
    clock.now += 5.0
    limiter.wait()

    assert clock.sleeps == []


def test_rate_limiter_measures_from_the_end_of_the_last_wait():
    clock = FakeClock()
    limiter = RateLimiter(3.0, clock=clock, sleep=clock.sleep)

    limiter.wait()
    limiter.wait()
    limiter.wait()

    assert clock.sleeps == [pytest.approx(3.0), pytest.approx(3.0)]


# --- Which pages to fetch ---------------------------------------------------------------------


def offsets(total: int, first_page_size: int = 25, max_full_tracks: int = 1000, tail_tracks: int = 300):
    return plan_page_offsets(
        total, first_page_size, max_full_tracks=max_full_tracks, tail_tracks=tail_tracks, page_size=50
    )


@pytest.mark.parametrize(
    ("total", "first_page_size", "expected"),
    [
        (127, 25, [25, 75, 125]),  # 75 + 50 stops at 124, so tracks 125-126 need a third page
        (70, 25, [25]),
        (25, 25, []),
        (10, 10, []),
        (0, 0, []),
        (127, 20, [20, 70, 120]),
        (1000, 25, list(range(25, 1000, 50))),
    ],
)
def test_small_playlists_are_fetched_in_full(total, first_page_size, expected):
    assert offsets(total, first_page_size) == expected


def test_big_playlists_fetch_only_the_last_tail_tracks():
    assert offsets(5931) == [5631, 5681, 5731, 5781, 5831, 5881]


def test_just_over_the_limit_switches_to_the_tail():
    assert offsets(1001) == [701, 751, 801, 851, 901, 951]


def test_tail_never_reaches_back_into_the_first_page():
    assert offsets(1100, tail_tracks=1090) == list(range(25, 1100, 50))


@pytest.mark.parametrize("status", [200, 204, 302])
def test_successful_statuses_do_not_raise(status):
    _raise_for_status(status, "Playlist x")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(404, "not_found"), (403, "blocked"), (429, "blocked"), (401, "http"), (500, "http"), (503, "http")],
)
def test_failed_statuses_map_to_error_kinds(status, kind):
    with pytest.raises(SpotifyFetchError, match=f"Playlist x.*{status}") as raised:
        _raise_for_status(status, "Playlist x")

    assert raised.value.kind == kind


def test_playlist_payload_unwraps_playlist_v2():
    payload = load(f"fetchPlaylist_{PLAYLIST_ID}.json")

    assert _playlist_v2_from_payload(payload, PLAYLIST_ID)["name"] == "IDM /  Braindance / Techno"


@pytest.mark.parametrize("playlist_v2", [None, {"__typename": "NotFound"}])
def test_playlist_payload_without_a_playlist_is_not_found(playlist_v2):
    with pytest.raises(SpotifyFetchError) as raised:
        _playlist_v2_from_payload({"data": {"playlistV2": playlist_v2}}, PLAYLIST_ID)

    assert raised.value.kind == "not_found"


def test_playlist_payload_with_graphql_errors_is_http_error():
    payload = {"errors": [{"message": "PersistedQueryNotFound"}], "data": None}

    with pytest.raises(SpotifyFetchError, match="PersistedQueryNotFound") as raised:
        _playlist_v2_from_payload(payload, PLAYLIST_ID)

    assert raised.value.kind == "http"


@pytest.mark.parametrize("payload", [[], {}, {"data": None}, {"data": {}}])
def test_playlist_payload_of_unexpected_shape_is_parse_error(payload):
    with pytest.raises(SpotifyFetchError) as raised:
        _playlist_v2_from_payload(payload, PLAYLIST_ID)

    assert raised.value.kind == "parse"


class FakeRequest:
    """Stands in for a Playwright request. Its `post_data_json` raises like the real one does."""

    def __init__(self, url: str, post_data: str | None):
        self.url = url
        self.post_data = post_data

    @property
    def post_data_json(self):
        raise Exception("POST data is not a valid JSON object")  # Playwright's Error, not ValueError


PATHFINDER = "https://api-partner.spotify.com/pathfinder/v2/query"


def test_json_body_reads_pathfinder_queries():
    request = FakeRequest(PATHFINDER, '{"operationName": "fetchPlaylist", "variables": {}}')

    assert _json_body(request) == {"operationName": "fetchPlaylist", "variables": {}}


@pytest.mark.parametrize(
    "post_data",
    [
        '{"sent_at":"2026-09-13T21:49:00.480Z"}\n{"type":"session"}',  # Sentry's newline-delimited envelope
        "not json",
        "[1, 2]",
        "",
        None,
    ],
)
def test_json_body_of_anything_else_is_empty(post_data):
    assert _json_body(FakeRequest(PATHFINDER, post_data)) == {}


class FlakyAction:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retries_are_not_used_when_the_first_attempt_works():
    action = FlakyAction("ok")

    assert call_with_retries(action, retries=1) == "ok"
    assert action.calls == 1


@pytest.mark.parametrize("kind", ["timeout", "http"])
def test_transient_failures_are_retried(kind):
    action = FlakyAction(SpotifyFetchError(kind, "flaky"), "ok")

    assert call_with_retries(action, retries=1) == "ok"
    assert action.calls == 2


def test_retries_give_up_with_the_last_error():
    action = FlakyAction(SpotifyFetchError("timeout", "first"), SpotifyFetchError("timeout", "second"))

    with pytest.raises(SpotifyFetchError, match="second"):
        call_with_retries(action, retries=1)
    assert action.calls == 2


@pytest.mark.parametrize("kind", ["not_found", "parse", "blocked"])
def test_permanent_failures_are_not_retried(kind):
    action = FlakyAction(SpotifyFetchError(kind, "nope"), "ok")

    with pytest.raises(SpotifyFetchError):
        call_with_retries(action, retries=3)
    assert action.calls == 1


def test_unexpected_exceptions_are_not_retried():
    action = FlakyAction(RuntimeError("bug"), "ok")

    with pytest.raises(RuntimeError):
        call_with_retries(action, retries=3)
    assert action.calls == 1


@pytest.mark.parametrize("total", [26, 127, 999, 1000, 1001, 1337, 5931])
def test_planned_offsets_are_unique_in_range_and_cover_the_end(total):
    planned = offsets(total)

    assert len(planned) == len(set(planned))
    assert all(25 <= offset < total for offset in planned)
    assert planned[-1] + 50 >= total


# --- Artist overviews -------------------------------------------------------------------------

ARTIST_ID = "6kBDZFXuLrZgHnvmPu9NsG"


@pytest.fixture
def artist_overview() -> dict:
    return load(f"queryArtistOverview_{ARTIST_ID}.json")


def overview_request(operation: str, url: str = PATHFINDER) -> FakeRequest:
    return FakeRequest(url, json.dumps({"operationName": operation, "variables": {"uri": "anything"}}))


def test_the_artist_overview_query_is_recognised():
    assert _is_artist_overview_request(overview_request("queryArtistOverview")) is True


@pytest.mark.parametrize(
    "operation", ["fetchPlaylist", "queryArtistDiscographyAll", "queryArtistOverviewV2", ""]
)
def test_another_query_on_the_same_endpoint_is_not_the_overview(operation):
    assert _is_artist_overview_request(overview_request(operation)) is False


def test_the_same_query_somewhere_other_than_pathfinder_is_ignored():
    request = overview_request("queryArtistOverview", url="https://open.spotify.com/artist/x")

    assert _is_artist_overview_request(request) is False


@pytest.mark.parametrize(
    "post_data",
    ['{"sent_at":"2026-09-21T02:00:00Z"}\n{"type":"session"}', "not json", "", None],
    ids=["sentry envelope", "not json", "empty", "none"],
)
def test_a_request_without_a_json_body_is_not_the_overview(post_data):
    assert _is_artist_overview_request(FakeRequest(PATHFINDER, post_data)) is False


def test_artist_payload_unwraps_the_artist_it_was_asked_for(artist_overview):
    artist_union = _artist_union_from_payload(artist_overview, ARTIST_ID)

    assert artist_union["profile"]["name"] == "Aphex Twin"


def test_a_payload_for_a_different_artist_is_a_parse_error(artist_overview):
    """The capture is matched on operation name alone, so the answer has to identify itself."""
    with pytest.raises(SpotifyFetchError) as raised:
        _artist_union_from_payload(artist_overview, "0000000000000000000000")

    assert raised.value.kind == "parse"
    assert "Aphex Twin" not in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"data": None}, {"data": {}}, {"data": {"artistUnion": None}}, {"data": {"artistUnion": {}}}],
    ids=["list", "empty", "no data", "no artist", "null artist", "artist with no uri"],
)
def test_an_artist_payload_of_unexpected_shape_is_a_parse_error(payload):
    with pytest.raises(SpotifyFetchError) as raised:
        _artist_union_from_payload(payload, ARTIST_ID)

    assert raised.value.kind == "parse"


def test_an_artist_payload_with_graphql_errors_is_an_http_error():
    payload = {"errors": [{"message": "PersistedQueryNotFound"}], "data": None}

    with pytest.raises(SpotifyFetchError, match="PersistedQueryNotFound") as raised:
        _artist_union_from_payload(payload, ARTIST_ID)

    assert raised.value.kind == "http"


@pytest.mark.parametrize(
    "artist_id",
    ["", "   ", "short", "spotify:artist:6kBDZFXuLrZgHnvmPu9NsG", "6kBDZFXuLrZgHnvmPu9Ns!"],
    ids=["empty", "blank", "too short", "a uri", "not base62"],
)
def test_fetch_artist_overview_rejects_a_bad_id_before_opening_a_browser(artist_id):
    # No context manager, so a browser would raise RuntimeError: the ValueError proves the
    # id is checked first, and that a typo never costs a page load.
    with pytest.raises(ValueError, match="artist ID"):
        SpotifyWebClient().fetch_artist_overview(artist_id)
