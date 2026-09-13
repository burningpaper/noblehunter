"""What the session holds, and the one rule about where a sign-in may send you next."""

from urllib.parse import urlsplit

from starlette.requests import Request

SESSION_COOKIE = "nh_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
SESSION_USER = "user"
SESSION_NEXT = "next"
SESSION_CSRF = "csrf"


def current_user(request: Request) -> dict | None:
    if "session" not in request.scope:
        return None
    return request.session.get(SESSION_USER)


def safe_next(value: str | None) -> str:
    """Only a path on this site. Anything that could leave it (//host, \\host, schemes) becomes '/'."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(character) < 0x20 for character in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value
