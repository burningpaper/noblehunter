"""Who can see what. The one place that decides.

People see their own artists' profiles and digests (and later their mail); admins, everyone
in ALLOWED_EMAILS, see everything. Every web route asks this module instead of filtering on
its own, and tests/test_web_access.py walks every route to check none forgot. Anything a
viewer can't see is reported exactly like something that doesn't exist, so nobody can learn
what another artist has.

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


class AdminOnly(PermissionError):
    """Only admins may do this. The web app answers 403."""


def viewer_for(session: Session, email: str, admin_emails: frozenset[str]) -> Viewer | None:
    """The viewer for a signed-in email, or None if that email has no access at all."""
    clean = email.strip().lower()
    if not clean:
        return None
    artist_ids = frozenset(
        session.scalars(
            select(ArtistMember.artist_id)
            .join(User, User.id == ArtistMember.user_id)
            .where(User.email == clean)
        )
    )
    is_admin = clean in admin_emails
    if not is_admin and not artist_ids:
        return None
    return Viewer(email=clean, is_admin=is_admin, artist_ids=artist_ids)


def record_sign_in(
    session: Session, *, email: str, name: str | None, picture_url: str | None, now: datetime
) -> User:
    """Create or refresh the user row for someone who just signed in."""
    clean = email.strip().lower()
    user = session.scalar(select(User).where(User.email == clean))
    if user is None:
        user = User(email=clean)
        session.add(user)
    user.name = name or user.name
    user.picture_url = picture_url
    user.last_signed_in_at = now
    session.flush()
    return user


def visible_to(viewer: Viewer, artist_column) -> ColumnElement[bool]:
    """A WHERE clause keeping rows on the viewer's artists: every row for an admin."""
    if viewer.is_admin:
        return true()
    return artist_column.in_(sorted(viewer.artist_ids))


def require_profile(session: Session, viewer: Viewer, profile_id: int) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None or not viewer.can_see_artist(profile.artist_id):
        raise NotVisible(f"Profile {profile_id} not found")
    return profile


def require_outreach(session: Session, viewer: Viewer, outreach_id: int) -> Outreach:
    outreach = session.get(Outreach, outreach_id)
    if outreach is None or not viewer.can_see_artist(outreach.profile.artist_id):
        raise NotVisible(f"Outreach {outreach_id} not found")
    return outreach


def require_admin(viewer: Viewer) -> None:
    if not viewer.is_admin:
        raise AdminOnly("Only admins can do that")
