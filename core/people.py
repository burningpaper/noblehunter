"""People and artists: who works on which artist.

Admins (everyone in ALLOWED_EMAILS) manage this on the People page. There are no invitation
emails: adding someone means their Google account is let in the next time they sign in. The
rules live here, not in the routes, so every way of changing membership obeys them, and
validation reports every problem at once in words an admin can act on.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from core.access import is_storable_id
from core.models import Artist, ArtistMember, Profile, User

EMAIL_PATTERN = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
MAX_EMAIL_LENGTH = 320
MAX_ARTIST_NAME_LENGTH = 80


class PeopleValidationError(ValueError):
    """One message per field, e.g. {"email": "Enter a valid email address"}."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


@dataclass(frozen=True)
class MemberSummary:
    user_id: int
    email: str
    name: str | None
    last_signed_in_at: datetime | None


@dataclass(frozen=True)
class ArtistSummary:
    id: int
    name: str
    members: tuple[MemberSummary, ...]
    profile_count: int


def normalize_email(value: str) -> str | None:
    email = value.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(email):
        return None
    return email


def list_artists(session: Session) -> list[ArtistSummary]:
    profile_counts = dict(
        session.execute(select(Profile.artist_id, func.count()).group_by(Profile.artist_id)).tuples().all()
    )
    artists = session.scalars(
        select(Artist)
        .options(selectinload(Artist.members).selectinload(ArtistMember.user))
        .order_by(func.lower(Artist.name), Artist.id)
    )
    return [_summary(artist, profile_counts.get(artist.id, 0)) for artist in artists]


def create_artist(session: Session, name: str) -> Artist:
    artist = Artist(name=_valid_artist_name(session, name, artist_id=None, field="name"))
    session.add(artist)
    session.flush()
    return artist


def rename_artist(session: Session, artist_id: int, name: str) -> Artist:
    if not is_storable_id(artist_id):
        raise LookupError(f"Artist {artist_id} not found")
    artist = session.get(Artist, artist_id)
    if artist is None:
        raise LookupError(f"Artist {artist_id} not found")
    artist.name = _valid_artist_name(session, name, artist_id=artist_id, field="name")
    session.flush()
    return artist


def add_member(
    session: Session, *, email: str, artist_id: int | None, added_by: str, new_artist_name: str = ""
) -> User:
    """Put someone on an artist: an existing one by id, or a new one by name.

    When `artist_id` is given, `new_artist_name` is ignored.
    """
    errors: dict[str, str] = {}
    clean_email = normalize_email(email)
    if clean_email is None:
        errors["email"] = "Enter a valid email address"

    artist = None
    if artist_id is not None:
        artist = session.get(Artist, artist_id) if is_storable_id(artist_id) else None
        if artist is None:
            errors["artist"] = "That artist no longer exists"
    elif not " ".join(new_artist_name.split()):
        errors["artist"] = "Choose an artist or name a new one"
    else:
        try:
            _valid_artist_name(session, new_artist_name, artist_id=None, field="artist")
        except PeopleValidationError as error:
            errors.update(error.errors)
    if errors:
        raise PeopleValidationError(errors)

    if artist is None:
        artist = create_artist(session, new_artist_name)
    user = session.scalar(select(User).where(User.email == clean_email))
    if user is None:
        user = User(email=clean_email)
        session.add(user)
        session.flush()
    elif session.get(ArtistMember, (artist.id, user.id)) is not None:
        raise PeopleValidationError({"email": f"{clean_email} is already on {artist.name}"})

    session.add(ArtistMember(artist_id=artist.id, user_id=user.id, added_by=added_by))
    session.flush()
    return user


def remove_member(session: Session, artist_id: int, user_id: int) -> None:
    """Take someone off an artist. Their user row stays, so history still says who they were."""
    if not (is_storable_id(artist_id) and is_storable_id(user_id)):
        raise LookupError(f"User {user_id} is not on artist {artist_id}")
    membership = session.get(ArtistMember, (artist_id, user_id))
    if membership is None:
        raise LookupError(f"User {user_id} is not on artist {artist_id}")
    session.delete(membership)
    session.flush()


def _valid_artist_name(session: Session, name: str, *, artist_id: int | None, field: str) -> str:
    clean = " ".join(name.split())
    if not clean:
        raise PeopleValidationError({field: "Give the artist a name"})
    if len(clean) > MAX_ARTIST_NAME_LENGTH:
        raise PeopleValidationError({field: f"Keep the name to {MAX_ARTIST_NAME_LENGTH} characters or fewer"})
    query = select(Artist.id).where(func.lower(Artist.name) == clean.lower())
    if artist_id is not None:
        query = query.where(Artist.id != artist_id)
    if session.scalar(query.limit(1)) is not None:
        raise PeopleValidationError({field: f"There's already an artist called “{clean}”"})
    return clean


def _summary(artist: Artist, profile_count: int) -> ArtistSummary:
    members = sorted(artist.members, key=lambda member: member.user.email)
    return ArtistSummary(
        id=artist.id,
        name=artist.name,
        members=tuple(
            MemberSummary(
                user_id=member.user.id,
                email=member.user.email,
                name=member.user.name,
                last_signed_in_at=member.user.last_signed_in_at,
            )
            for member in members
        ),
        profile_count=profile_count,
    )
