"""Small builders for database tests. Each flushes so ids and constraints apply immediately."""

from datetime import UTC, date, datetime
from itertools import count

from core.contacts import contact_key, email_domain_key
from core.models import Contact, Curator, Outreach, Playlist, PlaylistStatus, Profile

_sequence = count(1)


def now() -> datetime:
    return datetime.now(UTC)


def make_profile(session, name: str | None = None) -> Profile:
    profile = Profile(name=name or f"Profile {next(_sequence)}")
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
