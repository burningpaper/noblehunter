"""Who can see what. The one place meant to decide it.

The design: people see only their own artists' profiles and digests (and later their mail);
admins, everyone in ALLOWED_EMAILS, see everything. Anything a viewer can't see should be
reported exactly like something that doesn't exist, so nobody can learn what another artist
has.

Stage 1 (this module, today) only gets partway there. Every page and action except sign-in
requires a Viewer, and admin-only actions (the People page, Run now, the Claude budget) check
require_admin. But visible_to, require_profile and require_outreach aren't wired into any
route yet, so profiles, the digest and verdicts are still unfiltered: a signed-in member can
see and edit every artist's work, not just their own. Until stage 2 wires filtering into
every route and tests/test_web_access.py walks all of them to check none forgot, only admins
should be given access.

Access is worked out afresh on every request (see web/access.py), so taking someone off an
artist stops them on their next click, not when their session cookie expires.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, select, true
from sqlalchemy.orm import Session

from core.models import ArtistMember, Outreach, Profile, User


@dataclass(frozen=True)
class Viewer:
    email: str
    is_admin: bool
    artist_ids: frozenset[int]

    def can_see_artist(self, artist_id: int) -> bool:
        return self.is_admin or artist_id in self.artist_ids


class NotVisible(LookupError):
    """Missing, or on an artist the viewer isn't part of. The web app answers 404 either way."""


class AdminOnly(Exception):
    """Only admins may do this. The web app answers 403."""


# Postgres's `integer` range. An id outside this can't exist, so treat it as not found
# rather than let psycopg raise DataError and abort the transaction. This matches the
# `Integer` id columns on Profile and Outreach; change it if those become BigInteger.
MAX_POSTGRES_INT = 2**31 - 1

# users.name is String(200); truncate rather than let Postgres reject a long display name.
MAX_NAME_LENGTH = 200


def _clean_email(email: str) -> str | None:
    """A stripped, lowercased email, or None for empty or non-ASCII input.

    Checked before lowering: str.lower() maps some non-ASCII characters onto ASCII ones
    (e.g. the Kelvin sign lowercases to plain "k"), which could otherwise collide with
    someone else's address.
    """
    stripped = email.strip()
    if not stripped or not stripped.isascii():
        return None
    return stripped.lower()


def viewer_for(session: Session, email: str, admin_emails: frozenset[str]) -> Viewer | None:
    """The viewer for a signed-in email, or None if that email has no access at all."""
    clean = _clean_email(email)
    if clean is None:
        return None
    admins = frozenset(admin.strip().lower() for admin in admin_emails)
    artist_ids = frozenset(
        session.scalars(
            select(ArtistMember.artist_id)
            .join(User, User.id == ArtistMember.user_id)
            .where(User.email == clean)
        )
    )
    is_admin = clean in admins
    if not is_admin and not artist_ids:
        return None
    return Viewer(email=clean, is_admin=is_admin, artist_ids=artist_ids)


def record_sign_in(
    session: Session, *, email: str, name: str | None, picture_url: str | None, now: datetime
) -> User:
    """Create or refresh the user row for someone who just signed in."""
    clean = _clean_email(email)
    if clean is None:
        raise ValueError(f"Not a usable email: {email!r}")
    user = session.scalar(select(User).where(User.email == clean))
    if user is None:
        user = User(email=clean)
        session.add(user)
    user.name = (name or "")[:MAX_NAME_LENGTH] or user.name
    # picture_url deliberately mirrors Google's current value, including clearing it
    # (unlike name, which keeps the last known one when Google doesn't send one).
    user.picture_url = picture_url
    user.last_signed_in_at = now
    session.flush()
    return user


def visible_to(viewer: Viewer, artist_column: ColumnElement[int]) -> ColumnElement[bool]:
    """A WHERE clause keeping rows on the viewer's artists: every row for an admin.

    The query must already join in the table `artist_column` belongs to, or SQLAlchemy
    will silently cross-join it and this clause will not actually restrict anything;
    pytest is configured to turn that "cartesian product" warning into an error.
    """
    if viewer.is_admin:
        return true()
    return artist_column.in_(sorted(viewer.artist_ids))


def require_profile(session: Session, viewer: Viewer, profile_id: int) -> Profile:
    if not 0 < profile_id <= MAX_POSTGRES_INT:
        raise NotVisible(f"Profile {profile_id} not found")
    profile = session.get(Profile, profile_id)
    if profile is None or not viewer.can_see_artist(profile.artist_id):
        raise NotVisible(f"Profile {profile_id} not found")
    return profile


def require_outreach(session: Session, viewer: Viewer, outreach_id: int) -> Outreach:
    if not 0 < outreach_id <= MAX_POSTGRES_INT:
        raise NotVisible(f"Outreach {outreach_id} not found")
    outreach = session.get(Outreach, outreach_id)
    if outreach is None or not viewer.can_see_artist(outreach.profile.artist_id):
        raise NotVisible(f"Outreach {outreach_id} not found")
    return outreach


def require_admin(viewer: Viewer) -> None:
    if not viewer.is_admin:
        raise AdminOnly("Only admins can do that")
