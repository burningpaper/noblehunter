"""Discovery search: ask Google (via Serper) and Brave where the playlists are.

Spotify has no public "search playlists by vibe" API worth using, but search engines have
indexed millions of playlist pages. So for each search term we ask two engines for
`site:open.spotify.com/playlist <term>`, pull the 22-character playlist IDs out of the
result links, and merge what they found.

Two engines, because they barely agree: in Stage 0 they shared between zero and nine
playlists per term. Each engine is also unreliable in its own way. Serper sometimes hands
back short pages for no stated reason, and Brave's free tier allows roughly one request per
second. So nothing in this module raises on a bad day: a short page becomes a warning, a
failed provider becomes an error message, and the other provider carries on.

Error and warning messages are built only from provider names, page numbers and HTTP
status codes, never from response bodies or exception text, so an API key can't leak into
a log by accident.
"""

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

QUERY_PREFIX = "site:open.spotify.com/playlist "
PLAYLIST_URL = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?playlist/([A-Za-z0-9]{22})")
# Spotify's own editorial and algorithmic playlists. Nobody there reads pitches from us.
SPOTIFY_OWNED_PREFIX = "37i9dQZF1"

SERPER_URL = "https://google.serper.dev/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

# One polite pause before retrying a 429. Brave's free tier allows about one call a second.
RATE_LIMIT_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class SearchHit:
    spotify_id: str
    provider: str
    rank: int
    title: str
    snippet: str


@dataclass(frozen=True)
class Candidate:
    """One playlist worth looking at, with how each provider ranked it."""

    spotify_id: str
    hits: tuple[SearchHit, ...]

    @property
    def providers(self) -> frozenset[str]:
        return frozenset(hit.provider for hit in self.hits)

    @property
    def best_rank(self) -> int:
        return min(hit.rank for hit in self.hits)


@dataclass(frozen=True)
class DiscoveryResult:
    term: str
    candidates: tuple[Candidate, ...]
    spotify_owned_filtered: int
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ProviderResult:
    hits: tuple[SearchHit, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class SearchProvider(Protocol):
    name: str

    def search(self, query: str) -> ProviderResult: ...


@dataclass(frozen=True)
class _RawResult:
    """One search result, before we know whether it points at a playlist."""

    url: str
    title: str
    snippet: str


class _ProviderError(Exception):
    """A page failed. The message is safe to show: it never quotes what the server sent."""


def build_query(term: str) -> str:
    return f"{QUERY_PREFIX}{term.strip()}"


def discover(term: str, providers: Sequence[SearchProvider]) -> DiscoveryResult:
    """Ask every provider about one term and merge their playlists into ranked candidates."""
    query = build_query(term)
    results = [_search_safely(provider, query) for provider in providers]

    hits_by_playlist = _best_hits_by_playlist(hit for result in results for hit in result.hits)
    owned = [spotify_id for spotify_id in hits_by_playlist if spotify_id.startswith(SPOTIFY_OWNED_PREFIX)]
    candidates = [
        Candidate(spotify_id, tuple(hits))
        for spotify_id, hits in hits_by_playlist.items()
        if not spotify_id.startswith(SPOTIFY_OWNED_PREFIX)
    ]
    candidates.sort(key=lambda candidate: (candidate.best_rank, candidate.spotify_id))

    return DiscoveryResult(
        term=term.strip(),
        candidates=tuple(candidates),
        spotify_owned_filtered=len(owned),
        warnings=tuple(warning for result in results for warning in result.warnings),
        errors=tuple(error for result in results for error in result.errors),
    )


def _search_safely(provider: SearchProvider, query: str) -> ProviderResult:
    """Providers report their own failures, but a bug in one must not cost us the others."""
    try:
        return provider.search(query)
    except Exception as error:
        # Only the exception's type: its message might quote a key or a response body.
        return ProviderResult(errors=(f"{provider.name}: unexpected failure ({type(error).__name__})",))


def _best_hits_by_playlist(hits: Iterable[SearchHit]) -> dict[str, list[SearchHit]]:
    """Group hits by playlist, keeping one hit per provider: its best-ranked one, best first."""
    grouped: dict[str, list[SearchHit]] = {}
    for hit in sorted(hits, key=lambda hit: (hit.rank, hit.provider)):
        kept = grouped.setdefault(hit.spotify_id, [])
        if hit.provider not in {existing.provider for existing in kept}:
            kept.append(hit)
    return grouped


class _PagedProvider:
    """The paging loop both engines share. Subclasses fetch and parse a single page."""

    name = ""

    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        *,
        pages: int,
        per_page: int,
        delay_seconds: float,
        sleep: Callable[[float], None],
    ):
        self._api_key = api_key
        self._client = client
        self._pages = pages
        self._per_page = per_page
        self._delay_seconds = delay_seconds
        self._sleep = sleep

    def search(self, query: str) -> ProviderResult:
        hits: dict[str, SearchHit] = {}
        warnings: list[str] = []
        errors: list[str] = []
        rank = 0
        for page_index in range(self._pages):
            if page_index > 0:
                self._sleep(self._delay_seconds)
            try:
                results = self._fetch_page(query, page_index)
            except _ProviderError as error:
                errors.append(self._page_error(error, page_index))
                break
            if not results:
                if page_index == 0:
                    warnings.append(f"{self.name} returned no results for {_describe(query)}")
                break
            if len(results) < self._per_page:
                warnings.append(
                    f"{self.name} page {page_index + 1} for {_describe(query)} "
                    f"returned {len(results)} of {self._per_page} results"
                )
            for result in results:
                rank += 1
                self._keep_first_hit(hits, result, rank)
        return ProviderResult(hits=tuple(hits.values()), warnings=tuple(warnings), errors=tuple(errors))

    def _fetch_page(self, query: str, page_index: int) -> list[_RawResult]:
        raise NotImplementedError

    def _send(self, request: Callable[[], httpx.Response]) -> httpx.Response:
        """Make the call, retrying once after a pause if the provider says we're too fast."""
        response = _attempt(request)
        if response.status_code == 429:
            self._sleep(RATE_LIMIT_BACKOFF_SECONDS)
            response = _attempt(request)
        _raise_for_status(response)
        return response

    def _keep_first_hit(self, hits: dict[str, SearchHit], result: _RawResult, rank: int) -> None:
        """Ranks only grow as we page, so the first sighting of an ID is its best rank."""
        spotify_id = _playlist_id(result.url)
        if spotify_id and spotify_id not in hits:
            hits[spotify_id] = SearchHit(spotify_id, self.name, rank, result.title, result.snippet)

    def _page_error(self, error: _ProviderError, page_index: int) -> str:
        where = f" on page {page_index + 1}" if page_index > 0 else ""
        return f"{self.name}: {error}{where}"


class SerperProvider(_PagedProvider):
    name = "serper"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        *,
        pages: int = 3,
        per_page: int = 10,
        delay_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__(
            api_key, client, pages=pages, per_page=per_page, delay_seconds=delay_seconds, sleep=sleep
        )

    def _fetch_page(self, query: str, page_index: int) -> list[_RawResult]:
        # Serper pages are 1-based. Asking for 30 results in one call returns none at all.
        body = {"q": query, "num": self._per_page, "page": page_index + 1}
        response = self._send(
            lambda: self._client.post(SERPER_URL, headers={"X-API-KEY": self._api_key}, json=body)
        )
        organic = _list_of_objects(_json_object(response).get("organic", []), response)
        return [
            _RawResult(_text(item, "link"), _text(item, "title"), _text(item, "snippet")) for item in organic
        ]


class BraveProvider(_PagedProvider):
    name = "brave"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        *,
        pages: int = 2,
        per_page: int = 20,
        delay_seconds: float = 1.1,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__(
            api_key, client, pages=pages, per_page=per_page, delay_seconds=delay_seconds, sleep=sleep
        )

    def _fetch_page(self, query: str, page_index: int) -> list[_RawResult]:
        # Brave's `offset` is a page index (0, 1, ...), not a count of results to skip.
        headers = {"X-Subscription-Token": self._api_key, "Accept": "application/json"}
        params = {"q": query, "count": self._per_page, "offset": page_index}
        response = self._send(lambda: self._client.get(BRAVE_URL, headers=headers, params=params))
        web = _json_object(response).get("web", {})
        if not isinstance(web, dict):
            raise _malformed(response)
        results = _list_of_objects(web.get("results", []), response)
        return [
            _RawResult(_text(item, "url"), _text(item, "title"), _text(item, "description"))
            for item in results
        ]


def _attempt(request: Callable[[], httpx.Response]) -> httpx.Response:
    try:
        return request()
    except httpx.RequestError as error:
        # The exception's own text can include the request URL, so name only its type.
        raise _ProviderError(f"network error ({type(error).__name__})") from error


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status in (401, 403):
        raise _ProviderError(f"invalid API key (HTTP {status})")
    if status == 429:
        raise _ProviderError("rate limited (HTTP 429) even after a retry")
    if not response.is_success:
        raise _ProviderError(f"unexpected response (HTTP {status})")


def _json_object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise _malformed(response)
    return payload


def _list_of_objects(value: object, response: httpx.Response) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _malformed(response)
    return value


def _malformed(response: httpx.Response) -> _ProviderError:
    return _ProviderError(f"malformed response (HTTP {response.status_code})")


def _text(item: dict, key: str) -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def _describe(query: str) -> str:
    """Name the search the way a person would: by its term, not the site: incantation."""
    return repr(query.removeprefix(QUERY_PREFIX))


def _playlist_id(url: str) -> str | None:
    match = PLAYLIST_URL.search(url)
    return match.group(1) if match else None
