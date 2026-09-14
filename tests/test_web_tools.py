"""Web tools for contact research: fetch a page safely, and search the open web.

Nothing here touches the network. Each test gives the tools an `httpx.Client` whose transport
answers by URL, and a fake DNS lookup, so "is this address private?" is decided without
resolving anything real.
"""

import httpx
import pytest

from pipeline.web import FetchError, PageFetcher, WebResult, WebSearch, WebSearchError

PUBLIC_IP = "93.184.216.34"
FAKE_KEY = "fake-key-DO-NOT-LEAK-91b2"
ADDRESSES = {
    "linktr.ee": [PUBLIC_IP],
    "example.com": [PUBLIC_IP],
    "www.example.com": [PUBLIC_IP],
    "router.home": ["192.168.1.1"],
    "sneaky.example": [PUBLIC_IP, "10.0.0.5"],
}


def resolve(host: str) -> list[str]:
    return ADDRESSES.get(host, [PUBLIC_IP])


class RoutedTransport(httpx.MockTransport):
    """Answers each URL from a table and remembers every request it was sent."""

    def __init__(self, routes: dict[str, httpx.Response | Exception]):
        self.routes = routes
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if answer is None:
            raise AssertionError(f"Unexpected request: {request.url}")
        if isinstance(answer, Exception):
            raise answer
        return answer


def html(body: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, content=body.encode())


def fetcher(routes: dict, **options) -> tuple[PageFetcher, RoutedTransport]:
    transport = RoutedTransport(routes)
    return PageFetcher(httpx.Client(transport=transport), resolve=resolve, **options), transport


LINKTREE = """
<html><head><title>glitchlists | Linktree</title>
<style>.hidden { display: none }</style><script>var tracking = "do not read me";</script></head>
<body>
  <h1>@glitchlists</h1>
  <p>Curating IDM &amp; glitch since 2019. Submissions welcome.</p>
  <a href="https://www.instagram.com/glitchlists/">Instagram</a>
  <a href="mailto:hello@glitchlists.net">Email me</a>
  <a href="/glitchlists/more">More</a>
  <a href="https://www.instagram.com/glitchlists/">Instagram again</a>
  <a href="javascript:void(0)">Nothing</a>
</body></html>
"""


class TestFetchingAPage:
    def test_returns_title_visible_text_and_links(self):
        tool, _ = fetcher({"https://linktr.ee/glitchlists": html(LINKTREE)})

        page = tool.fetch("https://linktr.ee/glitchlists")

        assert page.url == "https://linktr.ee/glitchlists"
        assert page.title == "glitchlists | Linktree"
        assert "Curating IDM & glitch since 2019. Submissions welcome." in page.text
        assert "do not read me" not in page.text
        assert "display: none" not in page.text
        assert page.links == (
            "https://www.instagram.com/glitchlists/",
            "mailto:hello@glitchlists.net",
            "https://linktr.ee/glitchlists/more",
        )

    def test_follows_redirects_and_reports_where_it_landed(self):
        tool, transport = fetcher(
            {
                "http://example.com/curator": httpx.Response(
                    301, headers={"location": "https://www.example.com/c"}
                ),
                "https://www.example.com/c": html("<title>Curator</title><p>Hi</p>"),
            }
        )

        page = tool.fetch("http://example.com/curator")

        assert page.url == "https://www.example.com/c"
        assert page.title == "Curator"
        assert len(transport.requests) == 2

    def test_sends_a_descriptive_user_agent(self):
        tool, transport = fetcher({"https://example.com/": html("<p>ok</p>")})

        tool.fetch("https://example.com/")

        assert "NobleHunter" in transport.requests[0].headers["user-agent"]

    def test_plain_text_pages_are_fine(self):
        tool, _ = fetcher(
            {"https://example.com/about.txt": html("Email demos@example.com", content_type="text/plain")}
        )

        assert "demos@example.com" in tool.fetch("https://example.com/about.txt").text

    def test_a_huge_page_is_read_only_up_to_the_limit(self):
        body = "<p>" + ("word " * 50_000) + "</p><p>tail-marker</p>"
        tool, _ = fetcher({"https://example.com/big": html(body)}, max_bytes=10_000)

        page = tool.fetch("https://example.com/big")

        assert "tail-marker" not in page.text
        assert page.text.startswith("word word")

    def test_text_is_capped_for_the_model(self):
        tool, _ = fetcher(
            {"https://example.com/long": html("<p>" + ("x" * 5_000) + "</p>")}, max_text_chars=1_000
        )

        assert len(tool.fetch("https://example.com/long").text) <= 1_000


class TestWhatItRefuses:
    @pytest.mark.parametrize(
        "url", ["ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)", "not a url"]
    )
    def test_only_web_addresses(self, url):
        tool, transport = fetcher({})

        with pytest.raises(FetchError) as caught:
            tool.fetch(url)

        assert caught.value.kind == "bad-url"
        assert transport.requests == []

    @pytest.mark.parametrize(
        "url",
        [
            "http://router.home/admin",
            "http://127.0.0.1:8000/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://sneaky.example/",
        ],
    )
    def test_never_reaches_into_a_private_network(self, url):
        tool, transport = fetcher({})

        with pytest.raises(FetchError) as caught:
            tool.fetch(url)

        assert caught.value.kind == "blocked-address"
        assert transport.requests == []

    def test_a_redirect_into_a_private_network_is_refused(self):
        tool, transport = fetcher(
            {"https://example.com/go": httpx.Response(302, headers={"location": "http://192.168.1.1/"})}
        )

        with pytest.raises(FetchError) as caught:
            tool.fetch("https://example.com/go")

        assert caught.value.kind == "blocked-address"
        assert len(transport.requests) == 1

    def test_endless_redirects_stop(self):
        tool, _ = fetcher(
            {
                "https://example.com/a": httpx.Response(302, headers={"location": "https://example.com/b"}),
                "https://example.com/b": httpx.Response(302, headers={"location": "https://example.com/a"}),
            },
            max_redirects=3,
        )

        with pytest.raises(FetchError) as caught:
            tool.fetch("https://example.com/a")

        assert caught.value.kind == "too-many-redirects"

    @pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "application/octet-stream"])
    def test_non_pages_are_skipped(self, content_type):
        tool, _ = fetcher({"https://example.com/file": html("binary", content_type=content_type)})

        with pytest.raises(FetchError) as caught:
            tool.fetch("https://example.com/file")

        assert caught.value.kind == "not-html"

    def test_http_errors_say_which(self):
        tool, _ = fetcher({"https://example.com/gone": html("nope", status=404)})

        with pytest.raises(FetchError) as caught:
            tool.fetch("https://example.com/gone")

        assert caught.value.kind == "http"
        assert "404" in str(caught.value)

    def test_network_failures_are_reported_plainly(self):
        tool, _ = fetcher({"https://example.com/down": httpx.ConnectError("boom")})

        with pytest.raises(FetchError) as caught:
            tool.fetch("https://example.com/down")

        assert caught.value.kind == "network"


def serper_response(*items: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"organic": list(items)})


class TestWebSearch:
    def test_returns_titles_links_and_snippets(self):
        transport = RoutedTransport(
            {
                "https://google.serper.dev/search": serper_response(
                    {
                        "title": "glitchlists (@glitchlists) • Instagram",
                        "link": "https://www.instagram.com/glitchlists/",
                        "snippet": "Playlist curator. IDM & glitch.",
                    },
                    {"title": "No link here"},
                )
            }
        )
        search = WebSearch(FAKE_KEY, httpx.Client(transport=transport))

        results = search.search('"glitchlists" playlist curator')

        assert results == [
            WebResult(
                title="glitchlists (@glitchlists) • Instagram",
                url="https://www.instagram.com/glitchlists/",
                snippet="Playlist curator. IDM & glitch.",
            )
        ]
        request = transport.requests[0]
        assert request.headers["x-api-key"] == FAKE_KEY
        assert b"glitchlists" in request.content

    def test_nothing_found_is_an_empty_list(self):
        transport = RoutedTransport({"https://google.serper.dev/search": serper_response()})

        assert WebSearch(FAKE_KEY, httpx.Client(transport=transport)).search("nobody") == []

    @pytest.mark.parametrize(
        "answer",
        [
            httpx.Response(401, json={"message": "bad key"}),
            httpx.Response(200, content=b"<html>not json</html>"),
            httpx.ConnectError(f"could not reach https://google.serper.dev?key={FAKE_KEY}"),
        ],
    )
    def test_failures_never_leak_the_key(self, answer):
        transport = RoutedTransport({"https://google.serper.dev/search": answer})

        with pytest.raises(WebSearchError) as caught:
            WebSearch(FAKE_KEY, httpx.Client(transport=transport)).search("anything")

        assert FAKE_KEY not in str(caught.value)
