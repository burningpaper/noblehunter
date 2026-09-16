"""Small builders for database tests. Each flushes so ids and constraints apply immediately."""

from datetime import UTC, date, datetime
from itertools import count

from sqlalchemy import select

from core.access import Viewer
from core.contacts import contact_key, email_domain_key
from core.models import (
    Artist,
    ArtistMember,
    Contact,
    Curator,
    MailAccount,
    Outreach,
    Playlist,
    PlaylistStatus,
    Profile,
    User,
)

_sequence = count(1)


def now() -> datetime:
    return datetime.now(UTC)


DEFAULT_ARTIST = "Test Artist"


def make_artist(session, name: str | None = None) -> Artist:
    artist = Artist(name=name or f"Artist {next(_sequence)}")
    session.add(artist)
    session.flush()
    return artist


def default_artist_id(session) -> int:
    """The artist a test's profiles belong to when the test doesn't care which."""
    existing = session.scalar(select(Artist.id).where(Artist.name == DEFAULT_ARTIST))
    return existing if existing is not None else make_artist(session, DEFAULT_ARTIST).id


def make_user(session, email: str | None = None) -> User:
    user = User(email=email or f"person{next(_sequence)}@example.com")
    session.add(user)
    session.flush()
    return user


def make_member(session, artist: Artist, user: User | None = None) -> User:
    user = user or make_user(session)
    session.add(ArtistMember(artist_id=artist.id, user_id=user.id))
    session.flush()
    return user


def make_profile(session, name: str | None = None, artist: Artist | None = None) -> Profile:
    artist_id = artist.id if artist is not None else default_artist_id(session)
    profile = Profile(name=name or f"Profile {next(_sequence)}", artist_id=artist_id)
    session.add(profile)
    session.flush()
    return profile


def make_curator(session, **overrides) -> Curator:
    n = next(_sequence)
    curator = Curator(**{"display_name": f"Curator {n}", "spotify_user_id": f"user{n}", **overrides})
    session.add(curator)
    session.flush()
    return curator


def make_playlist(session, curator: Curator | None = None, **overrides) -> Playlist:
    n = next(_sequence)
    fields = {
        "spotify_id": f"{n:022d}",
        "name": f"Playlist {n}",
        "status": PlaylistStatus.CANDIDATE,
        "curator": curator,
        "last_checked_at": now(),
    }
    playlist = Playlist(**{**fields, **overrides})
    session.add(playlist)
    session.flush()
    return playlist


def make_outreach(
    session,
    curator: Curator,
    profile: Profile,
    digest_date: date,
    playlist: Playlist | None = None,
    **overrides,
) -> Outreach:
    outreach = Outreach(
        **{
            "curator": curator,
            "profile": profile,
            "playlist": playlist or make_playlist(session, curator=curator),
            "digest_date": digest_date,
            "brief_text": "A short brief.",
            **overrides,
        }
    )
    session.add(outreach)
    session.flush()
    return outreach


def make_contact(session, curator: Curator, route_type: str = "email", value: str | None = None) -> Contact:
    value = value or f"curator{next(_sequence)}@example-label.com"
    contact = Contact(
        curator=curator,
        route_type=route_type,
        value=value,
        contact_key=contact_key(route_type, value),
        domain_key=email_domain_key(value) if route_type == "email" else None,
        confidence="A",
        source_url="https://open.spotify.com/playlist/0000000000000000000001",
    )
    session.add(contact)
    session.flush()
    return contact


def make_mailbox(session, artist: Artist, address: str | None = None, **overrides) -> MailAccount:
    fields = {
        "artist_id": artist.id,
        "address": address or f"mailbox{next(_sequence)}@gmail.com",
        "refresh_token_encrypted": "encrypted-refresh-token",
        "history_id": "1000",
        **overrides,
    }
    mailbox = MailAccount(**fields)
    session.add(mailbox)
    session.flush()
    return mailbox


ADMIN_EMAIL = "owner@example.com"


def admin_viewer() -> Viewer:
    return Viewer(email=ADMIN_EMAIL, is_admin=True, artist_ids=frozenset())


def member_viewer(*artists: Artist, email: str = "member@example.com") -> Viewer:
    return Viewer(email=email, is_admin=False, artist_ids=frozenset(artist.id for artist in artists))
