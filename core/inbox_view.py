"""The Inbox: every conversation the viewer can see, the ones waiting on them first.

The digest asks "who should I write to tonight". This asks the other question -- "who is waiting
on me" -- and it's the page Jarred lives in once pitching is under way.

A conversation is an outreach entry with at least one message. Whose turn it is comes from the
direction of the last message, exactly as on the digest entry: derived, never a status column,
so a reply arriving overnight needs nothing to have been updated in the right order.

Every query is scoped with `visible_to`, so a member's Inbox can only ever hold their own.

The filter here is a *person*, not a profile as on the digest. The digest is about leads, which
belong to a profile; a conversation belongs to an artist's mailbox, and the question this page
answers is "who is waiting on me" -- so the person is the unit, and Jarred gets every reply on
every artist he works on in one place.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.access import Viewer, visible_to
from core.digest_view import EntryMail
from core.models import Artist, Curator, EmailMessage, MailDirection, Outreach, Playlist, Profile
from core.people import artist_ids_for_person, people_visible_to

SNIPPET_LENGTH = 160


@dataclass(frozen=True)
class ConversationView:
    outreach_id: int
    artist_id: int
    artist_name: str
    profile_name: str
    curator_name: str
    playlist_name: str
    waiting_on_you: bool
    last_at: datetime
    days_since_last: int
    last_snippet: str
    sent: int
    received: int
    unread: int


@dataclass(frozen=True)
class InboxView:
    conversations: tuple[ConversationView, ...]
    people: tuple[tuple[int, str], ...] = ()  # (id, label) of everyone the viewer may filter by
    chosen_person_id: int | None = None
    show_artists: bool = False

    @property
    def chosen_person_label(self) -> str:
        """What to call the person being filtered to, so an empty list can say whose it is."""
        return dict(self.people).get(self.chosen_person_id, "")

    @property
    def filter_query(self) -> str:
        """The filter as a query string, so "Check now" comes back to the list you were reading."""
        return f"?person={self.chosen_person_id}" if self.chosen_person_id else ""

    # The counts read from `conversations`, which is already filtered, so a narrowed list always
    # gets narrowed counts: the heading can't end up contradicting the list underneath it.
    @property
    def total(self) -> int:
        return len(self.conversations)

    @property
    def waiting(self) -> int:
        return sum(1 for conversation in self.conversations if conversation.waiting_on_you)

    @property
    def unread(self) -> int:
        return sum(conversation.unread for conversation in self.conversations)


def inbox_view(session: Session, viewer: Viewer, *, now: datetime, person_id: int | None = None) -> InboxView:
    """Every conversation this viewer can see, waiting-on-you first, then most recent.

    `person_id` narrows the list to one person. The caller checks it with `require_person`
    first, so someone the viewer can't see is a 404 long before it reaches this query.
    """
    last = _last_message_per_entry(session, viewer)
    counts = _counts_per_entry(session, list(last))
    rows = session.execute(
        select(
            Outreach.id,
            Profile.artist_id,
            Artist.name,
            Profile.name,
            Curator.display_name,
            Playlist.name,
        )
        .join(Profile, Profile.id == Outreach.profile_id)
        .join(Artist, Artist.id == Profile.artist_id)
        .join(Curator, Curator.id == Outreach.curator_id)
        .join(Playlist, Playlist.spotify_id == Outreach.playlist_id)
        .where(Outreach.id.in_(last), visible_to(viewer, Profile.artist_id))
    )
    everything = [
        ConversationView(
            outreach_id=outreach_id,
            artist_id=artist_id,
            artist_name=artist_name,
            profile_name=profile_name,
            curator_name=curator_name,
            playlist_name=playlist_name,
            # The same predicate the digest entry uses, not a second copy of it: two independent
            # derivations of "whose turn is it" disagree the first time either one changes.
            waiting_on_you=EntryMail(last_direction=last[outreach_id].direction).waiting_on_you,
            last_at=last[outreach_id].sent_at,
            days_since_last=max(0, (now - last[outreach_id].sent_at).days),
            last_snippet=_snippet(last[outreach_id].body_text),
            sent=counts[outreach_id][0],
            received=counts[outreach_id][1],
            unread=counts[outreach_id][2],
        )
        for outreach_id, artist_id, artist_name, profile_name, curator_name, playlist_name in rows
    ]
    # Mail lives on an artist's mailbox, so "this person's conversations" means every artist they
    # are a member of. Two people on one artist therefore see an identical list -- that follows
    # from mail belonging to a mailbox rather than to a person, and isn't worth designing around.
    theirs = artist_ids_for_person(session, person_id) if person_id is not None else None
    # `everything` is already scoped with visible_to, so narrowing it to their artists can only
    # ever remove rows: a person who also works on an artist the viewer can't see shows none of it.
    chosen = [c for c in everything if theirs is None or c.artist_id in theirs]
    chosen.sort(key=lambda c: (not c.waiting_on_you, -c.last_at.timestamp(), c.outreach_id))
    return InboxView(
        conversations=tuple(chosen),
        # Everyone the viewer may filter by, not just those with conversations: a person with a
        # quiet mailbox is still a real choice, and the empty state says so in their name.
        people=people_visible_to(session, viewer),
        chosen_person_id=person_id,
        show_artists=viewer.is_admin or len(viewer.artist_ids) > 1,
    )


@dataclass(frozen=True)
class _LastMessage:
    direction: str
    sent_at: datetime
    body_text: str


def _last_message_per_entry(session: Session, viewer: Viewer) -> dict[int, _LastMessage]:
    """The newest message on each visible conversation, one row per entry."""
    rows = session.execute(
        select(EmailMessage.outreach_id, EmailMessage.direction, EmailMessage.sent_at, EmailMessage.body_text)
        .join(Outreach, Outreach.id == EmailMessage.outreach_id)
        .join(Profile, Profile.id == Outreach.profile_id)
        .where(visible_to(viewer, Profile.artist_id))
        .order_by(EmailMessage.outreach_id, EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .distinct(EmailMessage.outreach_id)
    )
    return {
        outreach_id: _LastMessage(direction, sent_at, body_text)
        for outreach_id, direction, sent_at, body_text in rows
    }


def _counts_per_entry(session: Session, outreach_ids: list[int]) -> dict[int, tuple[int, int, int]]:
    """Sent, received and still-unread counts per conversation, in one query."""
    if not outreach_ids:
        return {}
    rows = session.execute(
        select(
            EmailMessage.outreach_id,
            func.count().filter(EmailMessage.direction == MailDirection.OUT),
            func.count().filter(EmailMessage.direction == MailDirection.IN),
            func.count().filter(EmailMessage.direction == MailDirection.IN, EmailMessage.read_at.is_(None)),
        )
        .where(EmailMessage.outreach_id.in_(outreach_ids))
        .group_by(EmailMessage.outreach_id)
    )
    return {outreach_id: (sent, received, unread) for outreach_id, sent, received, unread in rows}


def _snippet(body: str) -> str:
    text = " ".join((body or "").split())
    return text if len(text) <= SNIPPET_LENGTH else text[: SNIPPET_LENGTH - 1].rstrip() + "…"
