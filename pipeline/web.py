"""Web tools for contact research: fetch a page safely, and search the open web.

Research follows links that curators publish (a Linktree, a personal site, a label page) and
asks a search engine about names. Both run on the Mac Mini, inside Jarred's home network, and
the links come from strangers. So the fetcher is deliberately suspicious. It only fetches web
addresses, never anything inside a private network (a router's admin page, a cloud metadata
endpoint), and checks again after every redirect. Time, size and content type are all limited.

A page comes back as what a person would read (its title and visible text) plus every link
target, because a Linktree's buttons are nothing but links. The address check looks up DNS
just before each request, so a host that re-points in between could still slip through. That
risk is accepted: the fetcher only makes GET requests and reads text back.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; NobleHunter/1.0; playlist curator contact research)"
TIMEOUT_SECONDS = 10
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 5
MAX_TEXT_CHARS = 20_000
PAGE_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
LINK_SCHEMES = frozenset({"http", "https", "mailto"})
SERPER_URL = "https://google.serper.dev/search"
SEARCH_RESULTS = 10


class FetchError(Exception):
    """A page that couldn't be read.

    `kind` is one of: bad-url, blocked-address, too-many-redirects, not-html, http, network.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Page:
    url: str  # where it landed, after redirects
    title: str
    text: str
    links: tuple[str, ...]  # absolute http(s) and mailto targets, in page order, without repeats


def resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class PageFetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        resolve: Callable[[str], list[str]] = resolve_host,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        max_text_chars: int = MAX_TEXT_CHARS,
    ):
        self._client = client
        self._resolve = resolve
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._max_text_chars = max_text_chars

    def fetch(self, url: str) -> Page:
        current = url
        for _ in range(self._max_redirects + 1):
            self._check_address(current)
            try:
                with self._client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
                    timeout=TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers["location"])
                        continue
                    return self._read(current, response)
            except httpx.HTTPError as error:
                # The exception's own text can include the URL and its query string; name only the type.
                raise FetchError(
                    "network", f"Couldn't reach {_host(current)} ({type(error).__name__})"
                ) from None
        raise FetchError("too-many-redirects", f"More than {self._max_redirects} redirects from {_host(url)}")

    def _check_address(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise FetchError("bad-url", f"Not a web address: {url[:80]}")
        host = parts.hostname
        try:
            addresses = [host] if _is_ip_address(host) else self._resolve(host)
        except OSError:
            raise FetchError("network", f"Couldn't look up {host}") from None
        if not addresses or not all(_is_public(address) for address in addresses):
            raise FetchError(
                "blocked-address", f"{host} points inside a private network, so it wasn't fetched"
            )

    def _read(self, url: str, response: httpx.Response) -> Page:
        if response.status_code >= 400:
            raise FetchError("http", f"HTTP {response.status_code} from {_host(url)}")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in PAGE_TYPES:
            raise FetchError("not-html", f"{_host(url)} sent {content_type or 'an unknown type'}, not a page")

        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) >= self._max_bytes:
                del body[self._max_bytes :]
                break
        raw = _decode(bytes(body), response.charset_encoding)

        if content_type == "text/plain":
            return Page(url=url, title="", text=_collapse(raw)[: self._max_text_chars], links=())
        parser = _PageParser(url)
        parser.feed(raw)
        parser.close()
        return Page(
            url=url,
            title=_collapse("".join(parser.title_parts)),
            text=_collapse("".join(parser.text_parts))[: self._max_text_chars],
            links=tuple(parser.links),
        )


class _PageParser(HTMLParser):
    """Visible text, the title and link targets. Scripts, styles and the like are skipped."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skipping = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_TAGS:
            self.skipping += 1
            return
        if tag == "title":
            self.in_title = True
        elif tag == "a":
            self._add_link(dict(attrs).get("href"))
        self.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self.skipping = max(0, self.skipping - 1)
            return
        if tag == "title":
            self.in_title = False
        self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.skipping:
            return
        (self.title_parts if self.in_title else self.text_parts).append(data)

    def _add_link(self, href: str | None) -> None:
        if not href:
            return
        target = urljoin(self.base_url, href.strip())
        if urlsplit(target).scheme in LINK_SCHEMES and target not in self.links:
            self.links.append(target)


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


class WebSearchError(Exception):
    """The search didn't work. The message never includes the key."""


class WebSearch:
    """A general web search through Serper: one page of titles, links and snippets."""

    def __init__(self, api_key: str, client: httpx.Client, *, results: int = SEARCH_RESULTS):
        self._api_key = api_key
        self._client = client
        self._results = results

    def search(self, query: str) -> list[WebResult]:
        try:
            response = self._client.post(
                SERPER_URL,
                headers={"X-API-KEY": self._api_key},
                json={"q": query, "num": self._results},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise WebSearchError(f"Web search failed: network error ({type(error).__name__})") from None

        if response.status_code in (401, 403):
            raise WebSearchError(f"Web search failed: Serper refused the key (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise WebSearchError(f"Web search failed: HTTP {response.status_code}")
        try:
            organic = response.json().get("organic", [])
        except (ValueError, AttributeError):
            raise WebSearchError("Web search failed: Serper's answer wasn't readable JSON") from None
        if not isinstance(organic, list):
            raise WebSearchError("Web search failed: Serper's answer had an unexpected shape")
        return [_web_result(item) for item in organic if isinstance(item, dict) and _has_link(item)]


def _web_result(item: dict) -> WebResult:
    return WebResult(
        title=str(item.get("title") or ""), url=item["link"], snippet=str(item.get("snippet") or "")
    )


def _has_link(item: dict) -> bool:
    return isinstance(item.get("link"), str) and bool(item["link"])


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _is_public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_global
    except ValueError:
        return False


def _decode(body: bytes, charset: str | None) -> str:
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _host(url: str) -> str:
    return urlsplit(url).hostname or "that address"
