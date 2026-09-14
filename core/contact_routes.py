"""How to reach a curator: which of their contacts to use, and the link that opens it.

Shared by the pipeline's digest, which chooses the route for a brief, and the web app's digest
page, which shows it as a link. The most confident contact wins (A before B; C is never used).
When two are equally confident, the more direct route wins: an email or submission form before
a social profile.

A link is only ever a mail link or an http(s) link, so a stored value like `javascript:...` can
never turn into something clickable.
"""

from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Confidence, Contact

USABLE_GRADES = (Confidence.A, Confidence.B)
ROUTE_PREFERENCE = ("email", "submission-form", "instagram", "x", "bluesky", "other")
ROUTE_LABELS = {
    "email": "Email",
    "submission-form": "Submission form",
    "instagram": "Instagram",
    "x": "X",
    "bluesky": "Bluesky",
    "other": "Link",
}
PROFILE_LINKS = {
    "instagram": "https://www.instagram.com/{}/",
    "x": "https://x.com/{}",
    "bluesky": "https://bsky.app/profile/{}",
}


def best_contact(session: Session, curator_id: int) -> Contact | None:
    contacts = session.scalars(
        select(Contact).where(Contact.curator_id == curator_id, Contact.confidence.in_(USABLE_GRADES))
    ).all()
    return min(contacts, key=contact_preference, default=None)


def contact_preference(contact: Contact) -> tuple[int, int, int]:
    route_rank = (
        ROUTE_PREFERENCE.index(contact.route_type)
        if contact.route_type in ROUTE_PREFERENCE
        else len(ROUTE_PREFERENCE)
    )
    return (USABLE_GRADES.index(contact.confidence), route_rank, contact.id)


def contact_href(route_type: str, value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if route_type == "email":
        return f"mailto:{value}"
    if route_type in PROFILE_LINKS:
        return PROFILE_LINKS[route_type].format(value)

    scheme = urlsplit(value).scheme.lower()
    if scheme and scheme not in {"http", "https"}:
        return None
    url = value if scheme else f"https://{value}"
    host = urlsplit(url).hostname or ""
    return url if "." in host else None
