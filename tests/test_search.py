"""Discovery search: Serper and Brave find Spotify playlists, and `discover` merges them.

No test here touches the network. Each provider gets an `httpx.Client` whose transport
replays a script of canned responses, and a fake `sleep` that just records how long the
code asked to wait, so the rate-limit pauses cost nothing.
"""

import json

import httpx
import pytest

from pipeline.search import (
    RATE_LIMIT_BACKOFF_SECONDS,
    BraveProvider,
    ProviderResult,
    SearchHit,
    SerperProvider,
    build_query,
    discover,
)

FAKE_KEY = "fake-key-DO-NOT-LEAK-7f3a9c"
QUERY = "site:open.spotify.com/playlist glitchy ambient"


def pid(number: int) -> str:
    """A valid-looking 22-character Spotify playlist ID."""
    return f"playlist{number:0>14}"


def playlist_url(spotify_id: str) -> str:
    return f"https://open.spotify.com/playlist/{spotify_id}"


class ScriptedTransport(httpx.MockTransport):
    """Replays responses in order and remembers every request it was sent."""

    def __init__(self, *responses: httpx.Response | Exception):
        self.script = list(responses)
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.script:
            raise AssertionError(f"Unexpected extra request: {request.url}")
        response = self.script.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def serper_page(*urls: str) -> httpx.Response:
    organic = [
        {"title": f"Title {url}", "link": url, "snippet": f"Snippet {url}", "position": index}
        for index, url in enumerate(urls, start=1)
    ]
    return httpx.Response(200, json={"organic": organic})


def serper(transport: ScriptedTransport, sleeps: list[float], **options) -> SerperProvider:
    return SerperProvider(FAKE_KEY, httpx.Client(transport=transport), sleep=sleeps.append, **options)


def brave_page(*urls: str) -> httpx.Response:
    results = [{"title": f"Title {url}", "url": url, "description": f"Description {url}"} for url in urls]
    return httpx.Response(200, json={"web": {"results": results}})


def brave(transport: ScriptedTransport, sleeps: list[float], **options) -> BraveProvider:
    return BraveProvider(FAKE_KEY, httpx.Client(transport=transport), sleep=sleeps.append, **options)


class TestBuildQuery:
    def test_restricts_search_to_spotify_playlists(self):
        assert build_query("glitchy ambient") == QUERY

    def test_strips_surrounding_whitespace(self):
        assert build_query("  glitchy ambient \n") == QUERY


class TestSerperProvider:
    def test_parses_organic_results_with_ranks_across_pages(self):
        transport = ScriptedTransport(
            serper_page(playlist_url(pid(1)), playlist_url(pid(2))),
            serper_page(playlist_url(pid(3)), playlist_url(pid(4))),
        )
        provider = serper(transport, [], pages=2, per_page=2)

        result = provider.search(QUERY)

        assert [(hit.spotify_id, hit.rank) for hit in result.hits] == [
            (pid(1), 1),
            (pid(2), 2),
            (pid(3), 3),
            (pid(4), 4),
        ]
        first = result.hits[0]
        assert first.provider == "serper"
        assert first.title == f"Title {playlist_url(pid(1))}"
        assert first.snippet == f"Snippet {playlist_url(pid(1))}"
        assert result.warnings == ()
        assert result.errors == ()

    def test_sends_key_header_query_and_page_numbers(self):
        transport = ScriptedTransport(
            serper_page(playlist_url(pid(1))),
            serper_page(playlist_url(pid(2))),
            serper_page(playlist_url(pid(3))),
        )
        provider = serper(transport, [], per_page=1)

        provider.search(QUERY)

        assert [request.method for request in transport.requests] == ["POST"] * 3
        assert {str(request.url) for request in transport.requests} == {"https://google.serper.dev/search"}
        assert {request.headers["X-API-KEY"] for request in transport.requests} == {FAKE_KEY}
        assert [json.loads(request.content) for request in transport.requests] == [
            {"q": QUERY, "num": 1, "page": 1},
            {"q": QUERY, "num": 1, "page": 2},
            {"q": QUERY, "num": 1, "page": 3},
        ]

    def test_waits_between_page_calls_but_not_before_the_first(self):
        transport = ScriptedTransport(
            serper_page(playlist_url(pid(1))),
            serper_page(playlist_url(pid(2))),
            serper_page(playlist_url(pid(3))),
        )
        sleeps: list[float] = []
        provider = serper(transport, sleeps, per_page=1, delay_seconds=0.5)

        provider.search(QUERY)

        assert sleeps == [0.5, 0.5]


class TestBraveProvider:
    def test_parses_web_results_with_ranks_across_pages(self):
        transport = ScriptedTransport(
            brave_page(playlist_url(pid(1)), playlist_url(pid(2))),
            brave_page(playlist_url(pid(3))),
        )
        provider = brave(transport, [], pages=2, per_page=2)

        result = provider.search(QUERY)

        assert [(hit.spotify_id, hit.rank) for hit in result.hits] == [(pid(1), 1), (pid(2), 2), (pid(3), 3)]
        first = result.hits[0]
        assert first.provider == "brave"
        assert first.title == f"Title {playlist_url(pid(1))}"
        assert first.snippet == f"Description {playlist_url(pid(1))}"
        assert result.errors == ()

    def test_sends_subscription_header_count_and_offset_page_indexes(self):
        transport = ScriptedTransport(
            brave_page(playlist_url(pid(1)), playlist_url(pid(2))),
            brave_page(playlist_url(pid(3)), playlist_url(pid(4))),
        )
        provider = brave(transport, [], per_page=2)

        provider.search(QUERY)

        assert [request.method for request in transport.requests] == ["GET", "GET"]
        assert {request.url.path for request in transport.requests} == {"/res/v1/web/search"}
        assert {request.url.host for request in transport.requests} == {"api.search.brave.com"}
        assert [dict(request.url.params) for request in transport.requests] == [
            {"q": QUERY, "count": "2", "offset": "0"},
            {"q": QUERY, "count": "2", "offset": "1"},
        ]
        assert {request.headers["X-Subscription-Token"] for request in transport.requests} == {FAKE_KEY}
        assert {request.headers["Accept"] for request in transport.requests} == {"application/json"}

    def test_waits_between_page_calls_but_not_before_the_first(self):
        transport = ScriptedTransport(brave_page(playlist_url(pid(1))), brave_page(playlist_url(pid(2))))
        sleeps: list[float] = []
        provider = brave(transport, sleeps, per_page=1, delay_seconds=1.1)

        provider.search(QUERY)

        assert sleeps == [1.1]


class TestPlaylistExtraction:
    def test_ignores_results_that_are_not_playlists_but_still_counts_their_rank(self):
        transport = ScriptedTransport(
            serper_page(
                "https://open.spotify.com/artist/0OdUWJ0sBjDrqHygGUXeCF",
                "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX",
                "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
                f"https://example.com/playlist/{pid(9)}",
                playlist_url(pid(1)),
            )
        )
        provider = serper(transport, [], pages=1, per_page=5)

        result = provider.search(QUERY)

        assert [(hit.spotify_id, hit.rank) for hit in result.hits] == [(pid(1), 5)]

    def test_accepts_localised_playlist_urls_and_query_strings(self):
        transport = ScriptedTransport(
            serper_page(
                f"https://open.spotify.com/intl-de/playlist/{pid(1)}",
                f"https://open.spotify.com/intl-pt-br/playlist/{pid(2)}",
                f"https://open.spotify.com/playlist/{pid(3)}?si=a1b2c3d4e5f6",
            )
        )
        provider = serper(transport, [], pages=1, per_page=3)

        result = provider.search(QUERY)

        assert [hit.spotify_id for hit in result.hits] == [pid(1), pid(2), pid(3)]

    def test_duplicate_ids_within_a_provider_keep_the_best_rank(self):
        transport = ScriptedTransport(
            serper_page(playlist_url(pid(1)), playlist_url(pid(2))),
            serper_page(f"{playlist_url(pid(2))}?si=again", playlist_url(pid(1))),
        )
        provider = serper(transport, [], pages=2, per_page=2)

        result = provider.search(QUERY)

        assert [(hit.spotify_id, hit.rank) for hit in result.hits] == [(pid(1), 1), (pid(2), 2)]


class TestPaging:
    def test_short_page_records_a_warning_and_paging_continues(self):
        transport = ScriptedTransport(
            serper_page(playlist_url(pid(1))),
            serper_page(playlist_url(pid(2)), playlist_url(pid(3))),
        )
        provider = serper(transport, [], pages=2, per_page=2)

        result = provider.search(QUERY)

        assert [hit.spotify_id for hit in result.hits] == [pid(1), pid(2), pid(3)]
        assert result.warnings == ("serper page 1 for 'glitchy ambient' returned 1 of 2 results",)
        assert result.errors == ()

    def test_empty_page_stops_paging_without_a_warning(self):
        transport = ScriptedTransport(brave_page(playlist_url(pid(1))), brave_page())
        sleeps: list[float] = []
        provider = brave(transport, sleeps, pages=5, per_page=1)

        result = provider.search(QUERY)

        assert len(transport.requests) == 2
        assert sleeps == [1.1]
        assert [hit.spotify_id for hit in result.hits] == [pid(1)]
        assert result.warnings == ()

    def test_empty_first_page_is_worth_a_warning(self):
        transport = ScriptedTransport(serper_page())
        provider = serper(transport, [])

        result = provider.search(QUERY)

        assert len(transport.requests) == 1
        assert result.hits == ()
        assert result.warnings == ("serper returned no results for 'glitchy ambient'",)

    def test_results_missing_title_or_snippet_still_count(self):
        transport = ScriptedTransport(httpx.Response(200, json={"organic": [{"link": playlist_url(pid(1))}]}))
        provider = serper(transport, [], pages=1, per_page=1)

        result = provider.search(QUERY)

        assert [(hit.spotify_id, hit.title, hit.snippet) for hit in result.hits] == [(pid(1), "", "")]

    def test_brave_response_without_web_section_counts_as_empty(self):
        transport = ScriptedTransport(httpx.Response(200, json={"type": "search", "query": {}}))
        provider = brave(transport, [])

        result = provider.search(QUERY)

        assert result.hits == ()
        assert result.errors == ()


PROVIDER_FACTORIES = {"serper": (serper, serper_page), "brave": (brave, brave_page)}


@pytest.fixture(params=["serper", "brave"])
def provider_kind(request):
    return request.param


def make_provider(kind: str, transport: ScriptedTransport, sleeps: list[float], **options):
    factory, _ = PROVIDER_FACTORIES[kind]
    return factory(transport, sleeps, **options)


def page_for(kind: str, *urls: str) -> httpx.Response:
    _, page = PROVIDER_FACTORIES[kind]
    return page(*urls)


class TestProviderErrors:
    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_key_is_an_error_not_an_exception(self, provider_kind, status):
        transport = ScriptedTransport(httpx.Response(status, json={"message": f"bad key {FAKE_KEY}"}))
        provider = make_provider(provider_kind, transport, [])

        result = provider.search(QUERY)

        assert result.hits == ()
        assert result.errors == (f"{provider_kind}: invalid API key (HTTP {status})",)
        assert len(transport.requests) == 1

    def test_error_on_a_later_page_keeps_earlier_hits_and_stops_paging(self, provider_kind):
        transport = ScriptedTransport(
            page_for(provider_kind, playlist_url(pid(1))), httpx.Response(500, text="boom")
        )
        provider = make_provider(provider_kind, transport, [], pages=3, per_page=1)

        result = provider.search(QUERY)

        assert [hit.spotify_id for hit in result.hits] == [pid(1)]
        assert result.errors == (f"{provider_kind}: unexpected response (HTTP 500) on page 2",)
        assert len(transport.requests) == 2

    def test_rate_limit_is_retried_once_after_a_backoff(self, provider_kind):
        transport = ScriptedTransport(httpx.Response(429), page_for(provider_kind, playlist_url(pid(1))))
        sleeps: list[float] = []
        provider = make_provider(provider_kind, transport, sleeps, pages=1, per_page=1)

        result = provider.search(QUERY)

        assert [hit.spotify_id for hit in result.hits] == [pid(1)]
        assert result.errors == ()
        assert sleeps == [RATE_LIMIT_BACKOFF_SECONDS]
        assert len(transport.requests) == 2

    def test_rate_limit_that_persists_after_the_retry_is_an_error(self, provider_kind):
        transport = ScriptedTransport(httpx.Response(429), httpx.Response(429))
        sleeps: list[float] = []
        provider = make_provider(provider_kind, transport, sleeps)

        result = provider.search(QUERY)

        assert result.hits == ()
        assert result.errors == (f"{provider_kind}: rate limited (HTTP 429) even after a retry",)
        assert sleeps == [RATE_LIMIT_BACKOFF_SECONDS]
        assert len(transport.requests) == 2

    @pytest.mark.parametrize(
        "failure",
        [httpx.ConnectTimeout("timed out"), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
    )
    def test_network_failure_is_an_error(self, provider_kind, failure):
        transport = ScriptedTransport(failure)
        provider = make_provider(provider_kind, transport, [])

        result = provider.search(QUERY)

        assert result.hits == ()
        assert result.errors == (f"{provider_kind}: network error ({type(failure).__name__})",)

    def test_unexpected_status_reports_the_code_not_the_body(self, provider_kind):
        transport = ScriptedTransport(httpx.Response(502, text="<html>Upstream exploded</html>"))
        provider = make_provider(provider_kind, transport, [])

        result = provider.search(QUERY)

        assert result.errors == (f"{provider_kind}: unexpected response (HTTP 502)",)

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="<html>not json</html>"),
            httpx.Response(200, json=["not", "an", "object"]),
            httpx.Response(200, json={"organic": "nope", "web": {"results": "nope"}}),
            httpx.Response(200, json={"organic": ["nope"], "web": {"results": ["nope"]}}),
            httpx.Response(200, json={"organic": None, "web": "nope"}),
        ],
    )
    def test_malformed_response_is_an_error(self, provider_kind, response):
        transport = ScriptedTransport(response)
        provider = make_provider(provider_kind, transport, [])

        result = provider.search(QUERY)

        assert result.hits == ()
        assert result.errors == (f"{provider_kind}: malformed response (HTTP 200)",)


SPOTIFY_OWNED = "37i9dQZF1DXcBWIGoYBM5M"


def hit(spotify_id: str, provider: str, rank: int) -> SearchHit:
    return SearchHit(spotify_id, provider, rank, f"Title {rank}", f"Snippet {rank}")


class FakeProvider:
    """A provider that returns a canned result and remembers the queries it was asked."""

    def __init__(self, name: str, result: ProviderResult | Exception):
        self.name = name
        self.result = result
        self.queries: list[str] = []

    def search(self, query: str) -> ProviderResult:
        self.queries.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class TestDiscover:
    def test_searches_every_provider_with_the_site_restricted_query(self):
        serper_fake = FakeProvider("serper", ProviderResult())
        brave_fake = FakeProvider("brave", ProviderResult())

        result = discover("  glitchy ambient ", [serper_fake, brave_fake])

        assert serper_fake.queries == [QUERY]
        assert brave_fake.queries == [QUERY]
        assert result.term == "glitchy ambient"
        assert result.candidates == ()

    def test_same_playlist_from_both_providers_becomes_one_candidate(self):
        serper_fake = FakeProvider("serper", ProviderResult(hits=(hit(pid(1), "serper", 4),)))
        brave_fake = FakeProvider("brave", ProviderResult(hits=(hit(pid(1), "brave", 2),)))

        result = discover("glitchy ambient", [serper_fake, brave_fake])

        (candidate,) = result.candidates
        assert candidate.spotify_id == pid(1)
        assert candidate.providers == frozenset({"serper", "brave"})
        assert candidate.best_rank == 2
        assert [(h.provider, h.rank) for h in candidate.hits] == [("brave", 2), ("serper", 4)]

    def test_candidates_are_ordered_by_best_rank_then_id(self):
        serper_fake = FakeProvider(
            "serper",
            ProviderResult(
                hits=(hit(pid(3), "serper", 1), hit(pid(2), "serper", 2), hit(pid(5), "serper", 3))
            ),
        )
        brave_fake = FakeProvider(
            "brave", ProviderResult(hits=(hit(pid(1), "brave", 2), hit(pid(5), "brave", 1)))
        )

        result = discover("glitchy ambient", [serper_fake, brave_fake])

        assert [(c.spotify_id, c.best_rank) for c in result.candidates] == [
            (pid(3), 1),
            (pid(5), 1),
            (pid(1), 2),
            (pid(2), 2),
        ]

    def test_keeps_only_the_best_hit_per_provider(self):
        fake = FakeProvider(
            "custom", ProviderResult(hits=(hit(pid(1), "custom", 7), hit(pid(1), "custom", 3)))
        )

        result = discover("glitchy ambient", [fake])

        (candidate,) = result.candidates
        assert [(h.provider, h.rank) for h in candidate.hits] == [("custom", 3)]

    def test_spotify_owned_playlists_are_filtered_and_counted_once_each(self):
        serper_fake = FakeProvider(
            "serper", ProviderResult(hits=(hit(SPOTIFY_OWNED, "serper", 1), hit(pid(1), "serper", 2)))
        )
        brave_fake = FakeProvider(
            "brave",
            ProviderResult(hits=(hit(SPOTIFY_OWNED, "brave", 1), hit("37i9dQZF1E4zK5fYs2Ja0t", "brave", 2))),
        )

        result = discover("glitchy ambient", [serper_fake, brave_fake])

        assert [c.spotify_id for c in result.candidates] == [pid(1)]
        assert result.spotify_owned_filtered == 2

    def test_collects_warnings_and_errors_from_every_provider_in_order(self):
        serper_fake = FakeProvider(
            "serper", ProviderResult(warnings=("serper short",), errors=("serper broke",))
        )
        brave_fake = FakeProvider("brave", ProviderResult(warnings=("brave short",), errors=("brave broke",)))

        result = discover("glitchy ambient", [serper_fake, brave_fake])

        assert result.warnings == ("serper short", "brave short")
        assert result.errors == ("serper broke", "brave broke")

    def test_a_provider_that_raises_is_recorded_and_the_others_continue(self):
        broken = FakeProvider("broken", RuntimeError(f"exploded with {FAKE_KEY}"))
        working = FakeProvider("brave", ProviderResult(hits=(hit(pid(1), "brave", 1),)))

        result = discover("glitchy ambient", [broken, working])

        assert [c.spotify_id for c in result.candidates] == [pid(1)]
        assert result.errors == ("broken: unexpected failure (RuntimeError)",)

    def test_rejected_key_on_one_provider_does_not_stop_the_other(self):
        serper_provider = serper(ScriptedTransport(httpx.Response(401)), [])
        brave_provider = brave(ScriptedTransport(brave_page(playlist_url(pid(1)))), [], pages=1, per_page=1)

        result = discover("glitchy ambient", [serper_provider, brave_provider])

        assert [c.spotify_id for c in result.candidates] == [pid(1)]
        assert result.errors == ("serper: invalid API key (HTTP 401)",)

    def test_messages_never_contain_the_api_key(self):
        serper_provider = serper(
            ScriptedTransport(
                serper_page(playlist_url(pid(1))),
                httpx.Response(500, json={"error": f"key {FAKE_KEY} is wrong"}),
            ),
            [],
        )
        brave_provider = brave(ScriptedTransport(httpx.ConnectError(f"failed for token {FAKE_KEY}")), [])
        broken = FakeProvider("broken", ValueError(FAKE_KEY))

        result = discover("glitchy ambient", [serper_provider, brave_provider, broken])

        messages = result.warnings + result.errors
        assert len(result.warnings) == 1
        assert len(result.errors) == 3
        assert not any(FAKE_KEY in message for message in messages)
