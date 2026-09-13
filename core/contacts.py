"""Turn messy contact details into stable keys.

Curators write their contact details every which way: `@Handle`, a full Instagram URL
with tracking junk, `mailto:` links, an email with a full stop glued on the end. The
exclusion rules only work if every spelling of the same person reduces to the same key,
so this module is deliberately strict and boring: normalise, validate, or return None.
"""

import re
from urllib.parse import urlsplit

EMAIL_PATTERN = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
EMAIL_WRAPPING = "()<>[]{}.,;:'\" "

HANDLE_PATTERNS = {
    "instagram": re.compile(r"^[a-z0-9._]{1,30}$"),
    "x": re.compile(r"^[a-z0-9_]{1,15}$"),
    "bluesky": re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$"),
}
PLATFORM_HOSTS = {
    "instagram": {"instagram.com"},
    "x": {"x.com", "twitter.com"},
    "bluesky": {"bsky.app"},
}
# Path segments that are site features, not profiles (e.g. instagram.com/p/<post>).
RESERVED_PATHS = {
    "instagram": {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct"},
    "x": {
        "home",
        "i",
        "search",
        "explore",
        "settings",
        "intent",
        "share",
        "hashtag",
        "notifications",
        "messages",
    },
    "bluesky": set(),
}

FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "mail.com",
    "zoho.com",
    "fastmail.com",
    "hey.com",
    "msn.com",
}
FREE_EMAIL_FAMILIES = re.compile(r"^(yahoo|ymail|hotmail|outlook|live|gmx|yandex|aol)\.")

URL_ROUTES = {"submission-form", "other"}
HANDLE_ROUTES = set(HANDLE_PATTERNS)


def normalize_email(raw: str) -> str | None:
    value = raw.strip()
    if value.lower().startswith("mailto:"):
        value = value[len("mailto:") :]
    value = value.strip(EMAIL_WRAPPING).lower()
    return value if EMAIL_PATTERN.match(value) else None


def normalize_handle(platform: str, raw: str) -> str | None:
    if platform not in HANDLE_PATTERNS:
        raise ValueError(f"Unsupported platform: {platform!r}")

    value = raw.strip()
    if not value:
        return None

    candidate = _handle_from_url(platform, value) if "/" in value else value.lstrip("@")
    if candidate is None:
        return None

    candidate = candidate.lower()
    return candidate if HANDLE_PATTERNS[platform].match(candidate) else None


def _handle_from_url(platform: str, value: str) -> str | None:
    parts = urlsplit(value if "://" in value else f"https://{value}")
    host = _strip_www(parts.hostname or "")
    if host not in PLATFORM_HOSTS[platform]:
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    if platform == "bluesky":
        return segments[1] if len(segments) >= 2 and segments[0] == "profile" else None
    if not segments or segments[0].lower() in RESERVED_PATHS[platform]:
        return None
    return segments[0]


def normalize_url(raw: str) -> str | None:
    value = raw.strip()
    if not value or " " in value:
        return None

    parts = urlsplit(value if "://" in value else f"https://{value}")
    host = _strip_www((parts.hostname or "").lower())
    if "." not in host:
        return None
    return host + parts.path.rstrip("/").lower()


def contact_key(route_type: str, value: str) -> str | None:
    if route_type == "email":
        normalized = normalize_email(value)
        prefix = "email"
    elif route_type in HANDLE_ROUTES:
        normalized = normalize_handle(route_type, value)
        prefix = route_type
    elif route_type in URL_ROUTES:
        normalized = normalize_url(value)
        prefix = "url"
    else:
        raise ValueError(f"Unsupported route type: {route_type!r}")

    return f"{prefix}:{normalized}" if normalized else None


def email_domain_key(email: str) -> str | None:
    """A key for the email's domain, or None for free providers shared by millions of people."""
    normalized = normalize_email(email)
    if normalized is None:
        return None

    domain = normalized.split("@", 1)[1]
    if domain in FREE_EMAIL_DOMAINS or FREE_EMAIL_FAMILIES.match(domain):
        return None
    return f"domain:{domain}"


def _strip_www(host: str) -> str:
    host = host.lower()
    for prefix in ("www.", "mobile.", "m."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host
