"""The world the access walks run in, and a snapshot of everything in it a route could change.

Two artists. "Their Artist" has a full profile, a digest entry, a member and a failed run with
counts. "My Artist" has the outsider: a member with a profile and digest entry of their own, so
a 200 on their own pages means their own content really rendered.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from core.app_settings import nightly_claude_budget
from core.models import (
    AntiSignal,
    Artist,
    ArtistMember,
    Curator,
    EmailMessage,
    MailAccount,
    Outreach,
    Profile,
    ProfileGenre,
    ProfileTrack,
    ReferenceArtist,
    Run,
    RunRequest,
    RunStageCount,
    SearchTerm,
    User,
)
from tests.factories import (
    make_artist,
    make_contact,
    make_curator,
    make_mailbox,
    make_member,
    make_outreach,
    make_profile,
    make_user,
)
from tests.profile_helpers import add_contents
from tests.web_helpers import (
    FakeGmailSender,
    FakePitchWriter,
    FakeSuggester,
    app_client,
    member_client,
    sign_in,
    web_settings,
)

NIGHT = date(2026, 9, 15)
THEIR_ARTIST, THEIR_PROFILE, THEIR_BRIEF = "Their Artist", "Their Profile", "Their secret brief"
MY_PROFILE, MY_BRIEF = "My Profile", "My own brief"
RUN_ERROR_MARKER = "raw-run-error-naming-their-playlist"
OUTSIDER_EMAIL = "me@example.com"
NIGHT_IN_NUMBERS = "The night in numbers"
# Mail is configured in this world, so the Pitch mailbox routes really run. Otherwise an
# outsider's 404 on them could come from a missing MAIL_TOKEN_KEY rather than the access check.
MAIL_KEY = Fernet.generate_key().decode()
THEIR_CURATOR_EMAIL = "nina@broken-machines.com"


class _RefusingGmail:
    """Any send reaching this in an access walk is a leak; fail loudly rather than over the network."""

    def send(self, raw: str, *, thread_id: str | None = None):
        raise AssertionError("An access walk reached Gmail; a send leaked past require_outreach")


def world_settings():
    # SecretStr, not a bare string: model_copy skips validation, so the value has to arrive in
    # the shape pydantic would have stored it in.
    return web_settings().model_copy(update={"mail_token_key": SecretStr(MAIL_KEY)})


@dataclass
class World:
    ids: dict  # their ids, keyed the way tests/route_walk.fill_path expects
    profile_id: int
    outreach_id: int
    artist_id: int
    outsider_artist_id: int
    outsider_user_id: int


def build_world(session: Session) -> World:
    theirs = make_artist(session, THEIR_ARTIST)
    # Two genres, so a leaked move really reorders them.
    profile = add_contents(session, make_profile(session, THEIR_PROFILE, artist=theirs), genres=2)
    session.add(AntiSignal(profile=profile, kind="term", value="lofi"))
    session.flush()
    # A mailbox they pitch from, so attach and disconnect have something real to change.
    mailbox = make_mailbox(session, theirs, address="theirs@gmail.com")
    profile.mail_account_id = mailbox.id
    session.flush()
    owner = make_member(session, theirs, make_user(session, "them@example.com"))
    outreach = make_outreach(session, make_curator(session), profile, NIGHT, brief_text=THEIR_BRIEF)
    # An address a leaked send would really write to, on a mailbox they really pitch from.
    make_contact(session, outreach.curator, "email", THEIR_CURATOR_EMAIL)
    session.flush()
    _add_failed_run_with_counts(session, profile.id)

    mine = make_artist(session, "My Artist")
    outsider = make_member(session, mine, make_user(session, OUTSIDER_EMAIL))
    own = make_profile(session, MY_PROFILE, artist=mine)
    make_outreach(session, make_curator(session), own, NIGHT, brief_text=MY_BRIEF)

    first_genre = min(profile.genres, key=lambda genre: genre.priority)  # moving it "down" swaps the pair
    ids = {
        "profile_id": profile.id,
        "genre_id": first_genre.id,
        "reference_artist_id": profile.reference_artists[0].id,
        "signal_id": profile.anti_signals[0].id,
        "track_id": profile.tracks[0].id,
        "term_id": profile.search_terms[0].id,
        "section": "genres",
        "day": NIGHT.isoformat(),
        "outreach_id": outreach.id,
        "artist_id": theirs.id,
        "user_id": owner.id,
        "mail_account_id": mailbox.id,
    }
    return World(ids, profile.id, outreach.id, theirs.id, mine.id, outsider.id)


def _add_failed_run_with_counts(session: Session, profile_id: int) -> None:
    started = datetime.combine(NIGHT, time(0, 5), tzinfo=UTC)
    run = Run(
        trigger="schedule",
        status="failed",
        started_at=started,
        finished_at=started + timedelta(minutes=30),
        error=f"Timed out reading {RUN_ERROR_MARKER}",
    )
    session.add(run)
    session.flush()
    session.add(RunStageCount(run_id=run.id, profile_id=profile_id, stage="digest", count_in=3, count_out=1))
    session.flush()


def outsider_client(session: Session, suggester: FakeSuggester) -> TestClient:
    return member_client(
        session,
        OUTSIDER_EMAIL,
        suggester=suggester,
        settings=world_settings(),
        pitch_writer=FakePitchWriter(),
        # Nothing the outsider posts should ever reach Gmail; if it does, say so loudly rather
        # than open a socket to Google.
        gmail_for=lambda mailbox, cipher, settings, http: _RefusingGmail(),
    )


def admin_client(session: Session, suggester: FakeSuggester | None = None) -> TestClient:
    """Signed in as the admin. Sign in before taking a snapshot: signing in writes the user row."""
    # The admin is allowed to send, and the positive control needs the send to land somewhere --
    # on a fake that records it, never on Google.
    sender = FakeGmailSender()
    client = app_client(
        session,
        suggester=suggester or FakeSuggester(),
        settings=world_settings(),
        pitch_writer=FakePitchWriter(),
        gmail_for=lambda mailbox, cipher, settings, http: sender,
    )
    sign_in(client)
    return client


def seed_route_state(session: Session, world: World, method: str, path: str) -> None:
    """Put their data in the state this route would change, so a leak can't be a no-op."""
    profile = session.get(Profile, world.profile_id)
    if (method, path) == ("POST", "/profiles/{profile_id}/activate"):
        profile.is_active = False
    elif (method, path) == ("POST", "/profiles/{profile_id}/pause"):
        profile.is_active = True
    elif (method, path) in {
        ("POST", "/outreach/{outreach_id}/pitch/save"),
        ("POST", "/outreach/{outreach_id}/pitch/send"),
    }:
        entry = session.get(Outreach, world.outreach_id)
        entry.draft_subject, entry.draft_body = "Their subject", "Their draft"
    elif (method, path) == ("POST", "/profiles/{profile_id}/mail/attach"):
        # Point them at a different mailbox, so attaching the one the walk posts really moves it.
        spare = make_mailbox(session, session.get(Artist, world.artist_id), address="spare@gmail.com")
        profile.mail_account_id = spare.id
    session.flush()


def their_data(session: Session, world: World) -> dict:
    """Everything of theirs a web route could change, plus the settings and people every artist shares.

    This covers what the web role can write, at row level: a row added, removed or reordered
    shows up, and so does every column a form or verdict sets. It is not every column. Left out
    on purpose:
    - timestamps a legitimate write would move anyway, or that no web route sets: `created_at`,
      `profiles.updated_at`, `search_terms.decided_at`, `artist_members.added_at`;
    - columns derived from ones already compared: the `normalized_*` columns;
    - columns no web route writes: `search_terms.rationale`, `reference_artists.spotify_artist_id`,
      and an outreach entry's brief, angle, date, curator and playlist;
    - `users.name`, `picture_url` and `last_signed_in_at`, because signing in rewrites them. New or
      removed users still show up through the email list.
    """
    session.expire_all()
    profile = session.get(Profile, world.profile_id)
    outreach = session.get(Outreach, world.outreach_id)
    curator = session.get(Curator, outreach.curator_id)
    pid = world.profile_id
    return {
        "profile": (profile.name, profile.is_active, profile.digest_target, profile.min_followers),
        "their_profiles": sorted(
            session.scalars(select(Profile.name).where(Profile.artist_id == world.artist_id))
        ),
        "genres": [
            (g.tag, g.priority)
            for g in session.scalars(
                select(ProfileGenre).where(ProfileGenre.profile_id == pid).order_by(ProfileGenre.priority)
            )
        ],
        "reference_artists": sorted(
            session.scalars(select(ReferenceArtist.display_name).where(ReferenceArtist.profile_id == pid))
        ),
        "anti_signals": sorted(
            (s.kind, s.value) for s in session.scalars(select(AntiSignal).where(AntiSignal.profile_id == pid))
        ),
        "tracks": sorted(
            (t.title, t.spotify_url, t.description)
            for t in session.scalars(select(ProfileTrack).where(ProfileTrack.profile_id == pid))
        ),
        "terms": sorted(
            (t.term, t.status, t.origin)
            for t in session.scalars(select(SearchTerm).where(SearchTerm.profile_id == pid))
        ),
        "profile_mailbox": profile.mail_account_id,
        "mailboxes": sorted(
            (box.address, box.refresh_token_encrypted is not None, box.needs_reconnect)
            for box in session.scalars(select(MailAccount).where(MailAccount.artist_id == world.artist_id))
        ),
        "outreach": (outreach.status, outreach.status_changed_at, outreach.pitched_at, outreach.notes),
        "outreach_draft": (outreach.draft_subject, outreach.draft_body, outreach.gmail_thread_id),
        "email_messages": session.scalar(select(func.count()).select_from(EmailMessage)),
        "curator": (curator.excluded_at, curator.exclusion_reason),
        "budget": nightly_claude_budget(session),
        "artists": sorted(
            (artist.name, sorted(member.email for member in _members(session, artist.id)))
            for artist in session.scalars(select(Artist))
        ),
        "users": sorted(session.scalars(select(User.email))),
        "run_requests": session.scalar(select(func.count()).select_from(RunRequest)),
    }


def _members(session: Session, artist_id: int):
    return session.scalars(select(User).join(ArtistMember).where(ArtistMember.artist_id == artist_id))
