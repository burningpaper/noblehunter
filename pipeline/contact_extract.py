"""Contact research, step 1: read what the curator says about themselves.

A fair number of curators put the answer right in the description: an email after
"Submissions:", "Instagram: @handle", a Google Form. When they do, that is the best contact
there is, because it's the one they chose. Anything else that looks like a web address is a
lead to follow (a Linktree, a label site), not a contact in itself.

The order of work matters. Emails are found first and blanked out, so `gmail.com` never turns
up as a website. Web addresses go next, so `instagram.com/name` isn't read twice. Handles come
last and only count when the text names the platform: a bare `@someone` could be anyone on any
site, so it's left for the research agent to weigh.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.contacts import contact_key, normalize_email, normalize_handle


@dataclass(frozen=True)
class FoundRoute:
    route_type: str  # a core.models.RouteType value
    value: str

    @property
    def key(self) -> str | None:
        return contact_key(self.route_type, self.value)


@dataclass(frozen=True)
class Extraction:
    routes: tuple[FoundRoute, ...] = ()
    links: tuple[str, ...] = ()  # pages worth following, always with a scheme


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
OBFUSCATED_EMAIL = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*"
    r"([A-Za-z0-9-]+(?:(?:\s*[\[(]\s*dot\s*[\])]\s*|\.)[A-Za-z0-9-]+)+)",
    re.IGNORECASE,
)
DOT_WORD = re.compile(r"\s*[\[(]\s*dot\s*[\])]\s*", re.IGNORECASE)

# Bare domains ("maskedmortal.com") only count with a known ending, so "v0.2" and "e.g." don't.
TLDS = (
    "com", "org", "net", "io", "co", "fm", "me", "uk", "de", "fr", "nl", "eu", "app", "gg", "ly",
    "ee", "so", "gle", "link", "bio", "page", "site", "xyz", "info", "music", "band", "studio",
    "live", "tv", "cc", "ca", "au", "es", "se", "pl", "jp", "br", "ch", "dk", "fi", "ie", "nz",
)  # fmt: skip
URL_CHAR = r"[^\s<>\"'|•·]"
URL = re.compile(
    rf"(?:https?://|www\.){URL_CHAR}+"
    rf"|(?<![@\w.\-/])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    rf"(?:{'|'.join(sorted(TLDS, key=len, reverse=True))})\b(?:/{URL_CHAR}*)?",
    re.IGNORECASE,
)
TRAILING_PUNCTUATION = ".,;:!?"

SOCIAL_HOSTS = {"instagram.com": "instagram", "x.com": "x", "twitter.com": "x", "bsky.app": "bluesky"}
SPOTIFY_HOSTS = {"spotify.com", "spotify.link", "spoti.fi"}
FORM_HOSTS = {"forms.gle", "tally.so", "typeform.com", "jotform.com", "form.jotform.com"}

SEPARATOR = r"[\s:：\-–—]*"
HINTED_HANDLES = (
    ("instagram", re.compile(rf"\b(?:instagram|insta|ig){SEPARATOR}@([\w.]+)", re.IGNORECASE)),
    ("instagram", re.compile(r"@([\w.]+)\s+(?:on|at|via)\s+(?:instagram|insta|ig)\b", re.IGNORECASE)),
    ("x", re.compile(rf"\btwitter{SEPARATOR}@(\w+)", re.IGNORECASE)),
    ("x", re.compile(r"\bx\s*[:：]\s*@(\w+)", re.IGNORECASE)),
    ("x", re.compile(r"@(\w+)\s+on\s+(?:twitter|x)\b", re.IGNORECASE)),
    ("bluesky", re.compile(rf"\b(?:bluesky|bsky){SEPARATOR}@([\w.-]+)", re.IGNORECASE)),
)

type Hit = tuple[int, int, FoundRoute | str | None]  # start, end, and what was found there


def extract_contacts(text: str | None) -> Extraction:
    if not text:
        return Extraction()

    found: list[tuple[int, FoundRoute | str]] = []
    for scan in (_scan_emails, _scan_urls, _scan_handles):
        hits = scan(text)
        found.extend((start, item) for start, _, item in hits if item is not None)
        text = _blank_out(text, hits)
    found.sort(key=lambda pair: pair[0])
    return _collect(item for _, item in found)


def _scan_emails(text: str) -> list[Hit]:
    hits: list[Hit] = [(match.start(), match.end(), _email(match.group(0))) for match in EMAIL.finditer(text)]
    for match in OBFUSCATED_EMAIL.finditer(text):
        address = f"{match.group(1)}@{DOT_WORD.sub('.', match.group(2))}"
        hits.append((match.start(), match.end(), _email(address)))
    return hits


def _email(raw: str) -> FoundRoute | None:
    address = normalize_email(raw)
    return FoundRoute("email", address) if address else None


def _scan_urls(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for match in URL.finditer(text):
        url = _trim(match.group(0))
        if not url.lower().startswith(("http://", "https://")):
            url = f"https://{url}"
        hits.append((match.start(), match.end(), _classify(url)))
    return hits


def _trim(url: str) -> str:
    url = url.rstrip(TRAILING_PUNCTUATION)
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(TRAILING_PUNCTUATION)
    return url


def _classify(url: str) -> FoundRoute | str | None:
    """A route, a link to follow, or None for addresses that lead nowhere useful."""
    parts = urlsplit(url)
    host = _bare_host(parts.hostname or "")
    if host in SPOTIFY_HOSTS or host.endswith(".spotify.com"):
        return None
    if host in SOCIAL_HOSTS:
        platform = SOCIAL_HOSTS[host]
        handle = normalize_handle(platform, url)
        return FoundRoute(platform, handle) if handle else None  # e.g. a post, not a profile
    if host in FORM_HOSTS or host.endswith(".typeform.com") or _is_google_form(host, parts.path):
        return FoundRoute("submission-form", url)
    if host == "discord.gg" or (host == "discord.com" and parts.path.startswith("/invite/")):
        return FoundRoute("other", url)
    return url


def _is_google_form(host: str, path: str) -> bool:
    return host == "docs.google.com" and path.startswith("/forms/")


def _bare_host(host: str) -> str:
    for prefix in ("www.", "mobile.", "m."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def _scan_handles(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for platform, pattern in HINTED_HANDLES:
        for match in pattern.finditer(text):
            handle = normalize_handle(platform, match.group(1).rstrip("."))
            hits.append((match.start(), match.end(), FoundRoute(platform, handle) if handle else None))
    return hits


def _blank_out(text: str, hits: list[Hit]) -> str:
    chars = list(text)
    for start, end, _ in hits:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _collect(items) -> Extraction:
    routes: list[FoundRoute] = []
    links: list[str] = []
    seen: set[str | None] = set()
    for item in items:
        marker = item.key if isinstance(item, FoundRoute) else item
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(item, FoundRoute):
            routes.append(item)
        else:
            links.append(item)
    return Extraction(tuple(routes), tuple(links))
