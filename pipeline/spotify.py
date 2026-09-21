"""Read public Spotify playlists the way a logged-out visitor's browser does.

To judge whether a curator is worth pitching we need what the web player shows anyone: every
track with the date it was added, followers, description and owner. So we open the page in
headless Chromium, never logged in, and let the player make its own API call. We catch that
call, keep its anonymous headers in memory, and replay it for the rest of the track list.

The Stage 0 spike taught us four things, and each one shaped this module:

- Scrolling the page to load more tracks silently skipped some (108 of 127). Replaying
  `fetchPlaylistContents` with explicit offsets never did, so that is all we use.
- Tracks are not in date order, and paging a 5,931-track playlist took over three minutes.
  Big playlists get their first page plus their last `tail_tracks`, and are marked `partial`.
- A curator's profile pages can come back short mid-list and overlap at the edges. We keep
  paging until a page is empty, and de-duplicate by playlist ID.
- A missing playlist or user is a plain 404 page and the player never calls its API, so we
  check the document status instead of waiting out a timeout.

The same trick reads an artist page. `fetch_artist_overview` captures `queryArtistOverview`,
whose `discoveredOnV2` section names the playlists people found that artist through; the
reading of it lives in `pipeline.discovered_on`, because this module can only be tested live.

`fetch_artist_search` is its sibling over the search page's `searchArtists`, and exists because
the artist page needs an id while profiles store names. It hands the answer back unexamined:
whether a hit is the act somebody meant is `pipeline.artist_ids`' decision, and a careful one.

The page also loads reCAPTCHA. No challenge appeared at spike volume, and politeness keeps it
that way: one page load every few seconds and a pause between replayed pages. Tokens never
appear in errors or logs.

The parsing functions are pure and tested against real captured responses. Playwright is
imported only when a client starts, so they work without a browser installed.
"""

import json
import logging
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from playwright.sync_api import APIResponse, Browser, Page, Playwright, Request

logger = logging.getLogger(__name__)

PLAYER_URL = "https://open.spotify.com"
PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
PROFILE_API_URL = "https://spclient.wg.spotify.com/user-profile-view/v3/profile/"
ARTIST_OVERVIEW_OPERATION = "queryArtistOverview"
ARTIST_SEARCH_OPERATION = "searchArtists"
PLAYLIST_HEADERS = ("authorization", "client-token", "app-platform", "spotify-app-version", "content-type")
PROFILE_HEADERS = ("authorization", "client-token", "app-platform", "spotify-app-version")
PLAYLIST_PAGE_SIZE = 50
PROFILE_PAGE_SIZE = 200
MAX_PROFILE_PAGES = 100  # 20,000 playlists: far beyond any real curator, so hitting it means a paging bug

# Playlists, artists and users all wear the same 22-character base62 id, so one pattern checks
# any of them. It is a shape check, not an existence check: a well-formed id can still 404.
SPOTIFY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{22}$")
# Spotify's own editorial and algorithmic playlists. Nobody there reads pitches from us, so
# every discovery source drops them -- which is why the prefix lives here rather than in one.
SPOTIFY_OWNED_PREFIX = "37i9dQZF1"
TAG_PATTERN = re.compile(r"<[^>]*>")
RETRYABLE_KINDS = frozenset({"timeout", "http"})
BLOCKED_STATUSES = frozenset({403, 429})


@dataclass(frozen=True)
class Track:
    uid: str
    name: str | None
    artists: tuple[str, ...]  # empty for episodes and unavailable items
    added_at: datetime | None  # timezone-aware UTC


@dataclass(frozen=True)
class PlaylistData:
    spotify_id: str
    name: str
    description_html: str  # as delivered ("" if missing)
    description_text: str  # tags stripped, entities unescaped, whitespace collapsed
    owner_id: str | None
    owner_name: str | None
    followers: int | None
    total_tracks: int
    cover_image_url: str | None
    tracks: tuple[Track, ...]
    partial: bool  # True when only the first page and the tail were fetched


@dataclass(frozen=True)
class ProfilePlaylist:
    spotify_id: str
    name: str
    owner_id: str | None
    followers: int | None


class SpotifyFetchError(Exception):
    """A fetch that failed. `kind` is one of: timeout, not_found, http, parse, blocked."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# --- Parsing ----------------------------------------------------------------------------------


def parse_playlist(
    spotify_id: str, playlist_v2: dict, extra_items: Sequence[dict] = (), *, partial: bool = False
) -> PlaylistData:
    try:
        return _build_playlist(spotify_id, playlist_v2, extra_items, partial)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        # Name the problem, never the payload: responses carry curator names and other noise.
        detail = f"{type(error).__name__}: {error}"[:80]
        message = f"Playlist {spotify_id}: unexpected response shape ({detail})"
        raise SpotifyFetchError("parse", message) from None


def _build_playlist(
    spotify_id: str, playlist_v2: dict, extra_items: Sequence[dict], partial: bool
) -> PlaylistData:
    content = playlist_v2["content"]
    owner = (playlist_v2.get("ownerV2") or {}).get("data") or {}
    description_html = playlist_v2.get("description") or ""
    return PlaylistData(
        spotify_id=spotify_id,
        name=playlist_v2["name"],
        description_html=description_html,
        description_text=_html_to_text(description_html),
        owner_id=owner.get("username"),
        owner_name=owner.get("name"),
        followers=playlist_v2.get("followers"),
        total_tracks=int(content["totalCount"]),
        cover_image_url=_cover_image_url(playlist_v2.get("images")),
        tracks=_unique_tracks([*content["items"], *extra_items]),
        partial=partial,
    )


def _html_to_text(html: str) -> str:
    # Strip tags before unescaping, so an escaped "&lt;3" becomes "<3" instead of vanishing.
    without_tags = TAG_PATTERN.sub(" ", html)
    return " ".join(unescape(without_tags).split())


def _cover_image_url(images: dict | None) -> str | None:
    for image in (images or {}).get("items") or []:
        for source in image.get("sources") or []:
            if source.get("url"):
                return source["url"]
    return None


def _unique_tracks(items: Sequence[dict]) -> tuple[Track, ...]:
    """Pages can overlap when a playlist changes mid-fetch, so keep the first sighting of each uid."""
    tracks: dict[str, Track] = {}
    for item in items:
        track = _parse_track(item)
        tracks.setdefault(track.uid, track)
    return tuple(tracks.values())


def _parse_track(item: dict) -> Track:
    data = (item.get("itemV2") or {}).get("data") or {}
    is_track = data.get("__typename") == "Track"
    return Track(
        uid=item["uid"],
        name=data.get("name"),
        artists=_artist_names(data) if is_track else (),
        added_at=_parse_timestamp((item.get("addedAt") or {}).get("isoString")),
    )


def _artist_names(track_data: dict) -> tuple[str, ...]:
    artists = (track_data.get("artists") or {}).get("items") or []
    names = ((artist.get("profile") or {}).get("name") for artist in artists)
    return tuple(name for name in names if name)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        # Spotify sends "Z", but a zoneless stamp would otherwise be read as this machine's local time.
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_profile_playlists(pages: Sequence[Sequence[dict]]) -> list[ProfilePlaylist]:
    """Flatten profile pages into playlists, keeping the first sighting of each ID.

    Page boundaries can overlap, so the same playlist may arrive twice.
    """
    playlists: dict[str, ProfilePlaylist] = {}
    for page in pages:
        for item in page:
            playlist = _parse_profile_item(item)
            playlists.setdefault(playlist.spotify_id, playlist)
    return list(playlists.values())


def _parse_profile_item(item: dict) -> ProfilePlaylist:
    spotify_id = _id_from_uri(item.get("uri"), "playlist")
    if spotify_id is None:
        raise SpotifyFetchError("parse", "Profile playlist item has no usable playlist uri")
    return ProfilePlaylist(
        spotify_id=spotify_id,
        name=item.get("name") or "",
        owner_id=_id_from_uri(item.get("owner_uri"), "user"),
        followers=item.get("followers_count"),
    )


def _id_from_uri(uri: str | None, kind: str) -> str | None:
    """`spotify:playlist:abc` -> `abc`, or None if the uri is missing or of another kind."""
    prefix = f"spotify:{kind}:"
    if not uri or not uri.startswith(prefix):
        return None
    return uri.removeprefix(prefix) or None


def _playlist_v2_from_payload(payload: object, spotify_id: str) -> dict:
    """Unwrap `data.playlistV2`, turning Spotify's ways of saying "no" into typed errors."""
    what = f"Playlist {spotify_id}"
    if not isinstance(payload, dict):
        raise SpotifyFetchError("parse", f"{what}: response is not a JSON object")
    if payload.get("errors"):
        raise SpotifyFetchError("http", f"{what}: API returned errors ({_first_error_message(payload)})")
    data = payload.get("data")
    if not isinstance(data, dict) or "playlistV2" not in data:
        raise SpotifyFetchError("parse", f"{what}: response has no data.playlistV2")
    playlist_v2 = data["playlistV2"]
    if not playlist_v2 or playlist_v2.get("__typename") == "NotFound":
        raise SpotifyFetchError("not_found", f"{what}: not found")
    return playlist_v2


def _first_error_message(payload: dict) -> str:
    first = payload["errors"][0]
    message = first.get("message") if isinstance(first, dict) else None
    return str(message or "no message")[:100]


def _is_artist_overview_request(request: "Request") -> bool:
    """Spot the artist page asking for its own overview, by operation name alone.

    The playlist fetcher also matches on `variables.uri`, because the player fires
    `fetchPlaylist` for more than one playlist while a page is loading. An artist page asks
    for this operation for the artist whose page it is, and -- unlike a request variable,
    whose key we would be guessing from a response capture -- the answer names that artist
    itself. So identity is checked on the reply, where a fixture can prove the check works.
    """
    if not request.url.startswith(PATHFINDER_URL):
        return False
    return _json_body(request).get("operationName") == ARTIST_OVERVIEW_OPERATION


def artist_search_path(name: str) -> str:
    """The player's own artists-tab URL for a name, with every character encoded.

    `safe=''` rather than the default, which leaves "/" alone: an artist called "AC/DC" would
    otherwise be asked for as `/search/AC/DC/artists`, a different page entirely. Accents and
    spaces are the everyday case -- the reference artists include `Ólafur Arnalds`.
    """
    return f"/search/{quote(name, safe='')}/artists"


def _is_artist_search_request(request: "Request") -> bool:
    """Spot the search page asking for its artist results, by operation name alone.

    Matched the way `_is_artist_overview_request` is matched, and for the same reason: the
    fixture is a *response*, so it cannot say which key the request put the query under, and a
    guessed variable name fails as a thirty-second timeout rather than as an error.

    The overview then confirms identity on the reply, because `data.artistUnion.uri` names the
    artist. A search reply names only its results, so there is nothing here to confirm -- which
    is why `pipeline.artist_ids` checks the top hit's *name* against the name asked for. An
    answer to the wrong question fails that check and stores nothing.
    """
    if not request.url.startswith(PATHFINDER_URL):
        return False
    return _json_body(request).get("operationName") == ARTIST_SEARCH_OPERATION


def _artist_union_from_payload(payload: object, artist_id: str) -> dict:
    """Unwrap `data.artistUnion`, insisting it is the artist we actually asked for."""
    what = f"Artist {artist_id}"
    if not isinstance(payload, dict):
        raise SpotifyFetchError("parse", f"{what}: response is not a JSON object")
    if payload.get("errors"):
        raise SpotifyFetchError("http", f"{what}: API returned errors ({_first_error_message(payload)})")
    data = payload.get("data")
    artist_union = data.get("artistUnion") if isinstance(data, dict) else None
    if not isinstance(artist_union, dict):
        raise SpotifyFetchError("parse", f"{what}: response has no data.artistUnion")
    if artist_union.get("uri") != f"spotify:artist:{artist_id}":
        # Name no names: the payload carries an artist's name and playlist titles.
        raise SpotifyFetchError("parse", f"{what}: response is for a different artist")
    return artist_union


# --- Politeness and resilience ----------------------------------------------------------------


class RateLimiter:
    """Spaces calls at least `min_interval_seconds` apart, measured from the end of the last wait."""

    def __init__(self, min_interval_seconds: float, clock=time.monotonic, sleep=time.sleep):
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def call_with_retries[T](action: Callable[[], T], retries: int) -> T:
    """Run `action`, trying again up to `retries` times after a timeout or HTTP hiccup."""
    for attempt in range(1, retries + 1):
        try:
            return action()
        except SpotifyFetchError as error:
            if error.kind not in RETRYABLE_KINDS:
                raise
            logger.warning("Retrying after attempt %d of %d failed: %s", attempt, retries + 1, error)
    return action()


def plan_page_offsets(
    total: int, first_page_size: int, *, max_full_tracks: int, tail_tracks: int, page_size: int
) -> list[int]:
    """Offsets of the extra pages to fetch after the first page.

    Small playlists are fetched in full. Big ones get only their last `tail_tracks`: tracks are
    not in date order, so the tail is a sample from the far end rather than a guarantee of the
    newest adds, but paging thousands of tracks takes minutes.
    """
    start = first_page_size
    if total > max_full_tracks:
        start = max(first_page_size, total - tail_tracks)
    return list(range(start, total, page_size))


def _raise_for_status(status: int, what: str) -> None:
    if status < 400:
        return
    if status == 404:
        kind = "not_found"
    elif status in BLOCKED_STATUSES:
        kind = "blocked"
    else:
        kind = "http"
    raise SpotifyFetchError(kind, f"{what}: HTTP {status}")


@contextmanager
def _browser_errors_as_fetch_errors(what: str) -> Iterator[None]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        yield
    except PlaywrightTimeoutError:
        raise SpotifyFetchError("timeout", f"{what}: timed out") from None
    except PlaywrightError as error:
        # First line only: Playwright appends a call log that can be long.
        summary = str(error).strip().splitlines()[0][:120] if str(error).strip() else "unknown"
        raise SpotifyFetchError("http", f"{what}: browser error ({summary})") from None


# --- The browser client -----------------------------------------------------------------------


class SpotifyWebClient:
    """Headless, logged-out Chromium that fetches playlists and profiles. Use as a context manager.

    Open one client per thread at a time and reuse it: Playwright's sync API refuses to start a
    second instance while one is running. Each fetch still gets a fresh browser context.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float = 3.0,
        page_delay_seconds: float = 1.0,
        navigation_timeout_seconds: float = 30.0,
        max_full_tracks: int = 1000,
        tail_tracks: int = 300,
        retries: int = 1,
    ):
        self._limiter = RateLimiter(min_interval_seconds)
        self._page_delay_seconds = page_delay_seconds
        self._timeout_seconds = navigation_timeout_seconds
        self._max_full_tracks = max_full_tracks
        self._tail_tracks = tail_tracks
        self._retries = retries
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "SpotifyWebClient":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception:
            self._playwright.stop()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = self._playwright = None

    def fetch_playlist(self, spotify_id: str) -> PlaylistData:
        if not SPOTIFY_ID_PATTERN.match(spotify_id):
            raise ValueError(f"Not a Spotify playlist ID: {spotify_id!r}")
        return call_with_retries(lambda: self._fetch_playlist_once(spotify_id), self._retries)

    def fetch_user_playlists(self, user_id: str, *, owned_only: bool = True) -> list[ProfilePlaylist]:
        if not user_id or not user_id.strip():
            raise ValueError("A Spotify user ID is required")
        playlists = call_with_retries(lambda: self._fetch_user_playlists_once(user_id), self._retries)
        if owned_only:
            return [playlist for playlist in playlists if playlist.owner_id == user_id]
        return playlists

    def fetch_artist_overview(self, artist_id: str) -> dict:
        """The artist page's own `queryArtistOverview` response, exactly as Spotify sent it.

        Handed back whole rather than parsed, because the only thing that reads it --
        `pipeline.discovered_on` -- is tested against captured payloads and this is not.
        """
        if not SPOTIFY_ID_PATTERN.match(artist_id):
            raise ValueError(f"Not a Spotify artist ID: {artist_id!r}")
        return call_with_retries(lambda: self._fetch_artist_overview_once(artist_id), self._retries)

    def fetch_artist_search(self, name: str) -> object:
        """The search page's own `searchArtists` response, exactly as Spotify sent it.

        Handed back whole and untyped, because nothing in the reply identifies the query: there
        is no check to make here that `pipeline.artist_ids` does not make better, against the
        name that was asked for.
        """
        if not name or not name.strip():
            raise ValueError("An artist name is required to search")
        return call_with_retries(lambda: self._fetch_artist_search_once(name), self._retries)

    # Playlists

    def _fetch_playlist_once(self, spotify_id: str) -> PlaylistData:
        what = f"Playlist {spotify_id}"
        uri = f"spotify:playlist:{spotify_id}"

        def is_first_query(request: "Request") -> bool:
            if not request.url.startswith(PATHFINDER_URL):
                return False
            body = _json_body(request)
            variables = body.get("variables")
            return (
                body.get("operationName") == "fetchPlaylist"
                and isinstance(variables, dict)
                and variables.get("uri") == uri
            )

        with self._player_page() as page, _browser_errors_as_fetch_errors(what):
            request = self._load_and_capture(page, f"/playlist/{spotify_id}", is_first_query, what)
            playlist_v2 = _playlist_v2_from_payload(self._response_json(request, what), spotify_id)
            items = _content_items(playlist_v2, what)
            total = int((playlist_v2.get("content") or {}).get("totalCount") or 0)
            offsets = plan_page_offsets(
                total,
                len(items),
                max_full_tracks=self._max_full_tracks,
                tail_tracks=self._tail_tracks,
                page_size=PLAYLIST_PAGE_SIZE,
            )
            headers = _forwarded_headers(request, PLAYLIST_HEADERS)
            first_body = _json_body(request)
            extra_items = [
                item
                for offset in offsets
                for item in self._fetch_contents_page(page, headers, first_body, offset, total, what)
            ]
        return parse_playlist(spotify_id, playlist_v2, extra_items, partial=total > self._max_full_tracks)

    def _fetch_contents_page(
        self, page: "Page", headers: dict, first_body: dict, offset: int, total: int, what: str
    ) -> list[dict]:
        time.sleep(self._page_delay_seconds)
        variables = {**first_body["variables"], "offset": offset, "limit": PLAYLIST_PAGE_SIZE}
        variables.pop("enableWatchFeedEntrypoint", None)
        body = {
            "variables": variables,
            "operationName": "fetchPlaylistContents",
            "extensions": first_body["extensions"],
        }
        page_what = f"{what} (tracks from {offset})"
        response = page.request.post(
            PATHFINDER_URL, headers=headers, data=json.dumps(body), timeout=self._timeout_ms
        )
        items = _content_items(_playlist_v2_from_payload(_api_json(response, page_what), what), page_what)
        expected = min(PLAYLIST_PAGE_SIZE, total - offset)
        if len(items) < expected:
            logger.warning("%s: expected %d items, got %d", page_what, expected, len(items))
        return items

    # Profiles

    def _fetch_user_playlists_once(self, user_id: str) -> list[ProfilePlaylist]:
        what = f"User {user_id}"
        profile_url = f"{PROFILE_API_URL}{quote(user_id, safe='')}"

        def is_profile_query(request: "Request") -> bool:
            return request.url.startswith(profile_url)

        with self._player_page() as page, _browser_errors_as_fetch_errors(what):
            request = self._load_and_capture(page, f"/user/{quote(user_id, safe='')}", is_profile_query, what)
            headers = _forwarded_headers(request, PROFILE_HEADERS)
            # The player asks for protobuf; the same endpoint serves JSON when asked.
            headers["accept"] = "application/json"
            pages = self._fetch_profile_pages(page, headers, profile_url, what)
        return parse_profile_playlists(pages)

    def _fetch_profile_pages(
        self, page: "Page", headers: dict, profile_url: str, what: str
    ) -> list[list[dict]]:
        pages: list[list[dict]] = []
        offset = 0
        for _ in range(MAX_PROFILE_PAGES):
            url = f"{profile_url}/playlists?offset={offset}&limit={PROFILE_PAGE_SIZE}&market=from_token"
            response = page.request.get(url, headers=headers, timeout=self._timeout_ms)
            batch = _profile_batch(_api_json(response, f"{what} (playlists from {offset})"), what)
            # Pages can be shorter than the limit mid-list, so only an empty page means done.
            if not batch:
                return pages
            pages.append(batch)
            offset += len(batch)
            time.sleep(self._page_delay_seconds)
        raise SpotifyFetchError(
            "parse", f"{what}: playlist paging did not end after {MAX_PROFILE_PAGES} pages"
        )

    # Artists

    def _fetch_artist_overview_once(self, artist_id: str) -> dict:
        what = f"Artist {artist_id}"
        path = f"/artist/{artist_id}"

        with self._player_page() as page, _browser_errors_as_fetch_errors(what):
            request = self._load_and_capture(page, path, _is_artist_overview_request, what)
            payload = self._response_json(request, what)
        # Checked after the page closes: a wrong or empty answer is a parse problem, not a
        # browser one, and this is the check that lets the capture match on name alone.
        _artist_union_from_payload(payload, artist_id)
        return payload

    def _fetch_artist_search_once(self, name: str) -> object:
        what = f"Artist search for {name!r}"

        with self._player_page() as page, _browser_errors_as_fetch_errors(what):
            request = self._load_and_capture(page, artist_search_path(name), _is_artist_search_request, what)
            return self._response_json(request, what)

    # Shared browser plumbing

    @property
    def _timeout_ms(self) -> float:
        return self._timeout_seconds * 1000

    @contextmanager
    def _player_page(self) -> Iterator["Page"]:
        """A fresh browser context per fetch, so no state leaks from one playlist to the next."""
        if self._browser is None:
            raise RuntimeError(
                "Use SpotifyWebClient as a context manager: `with SpotifyWebClient() as client:`"
            )
        self._limiter.wait()
        context = self._browser.new_context(locale="en-US")
        try:
            yield context.new_page()
        finally:
            context.close()

    def _load_and_capture(
        self, page: "Page", path: str, is_wanted: Callable[["Request"], bool], what: str
    ) -> "Request":
        """Open a player page and return the first request it makes that `is_wanted` accepts."""
        captured: list[Request] = []

        def on_request(request: "Request") -> None:
            if not captured and is_wanted(request):
                captured.append(request)

        page.on("request", on_request)
        navigation = page.goto(f"{PLAYER_URL}{path}", wait_until="domcontentloaded", timeout=self._timeout_ms)
        if navigation is not None:
            _raise_for_status(navigation.status, what)

        deadline = time.monotonic() + self._timeout_seconds
        while not captured:
            if time.monotonic() > deadline:
                raise SpotifyFetchError("timeout", f"{what}: the player never requested its data")
            page.wait_for_timeout(250)
        return captured[0]

    def _response_json(self, request: "Request", what: str) -> object:
        response = request.response()
        if response is None:
            raise SpotifyFetchError("http", f"{what}: the player's request got no response")
        _raise_for_status(response.status, what)
        try:
            return response.json()
        except ValueError:
            raise SpotifyFetchError("parse", f"{what}: response is not JSON") from None


def _json_body(request: "Request") -> dict:
    """The request's JSON object body, or {} for anything else.

    We decode `post_data` ourselves: Playwright's `post_data_json` raises its own error type on the
    player's Sentry telemetry (newline-delimited JSON), which escaped our request listener live.
    """
    try:
        body = json.loads(request.post_data or "")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _forwarded_headers(request: "Request", names: Sequence[str]) -> dict:
    return {name: value for name, value in request.all_headers().items() if name in names}


def _api_json(response: "APIResponse", what: str) -> object:
    _raise_for_status(response.status, what)
    try:
        return response.json()
    except ValueError:
        raise SpotifyFetchError("parse", f"{what}: response is not JSON") from None


def _content_items(playlist_v2: dict, what: str) -> list[dict]:
    items = (playlist_v2.get("content") or {}).get("items")
    if not isinstance(items, list):
        raise SpotifyFetchError("parse", f"{what}: response has no content.items")
    return items


def _profile_batch(payload: object, what: str) -> list[dict]:
    batch = payload.get("public_playlists", []) if isinstance(payload, dict) else None
    if not isinstance(batch, list):
        raise SpotifyFetchError("parse", f"{what}: playlists response has no public_playlists list")
    return batch
