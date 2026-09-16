"""How many conversations a profile has open, and how many new ones tonight can start.

Jarred, 2026-09-16: writing twenty emails is easy; sustaining twenty conversations is not. So
the digest works to a ceiling rather than a flat nightly number -- top the open conversations up
to `open_conversation_limit`, and never hand over more than `digest_target` in one night.

Two judgement calls, made here so the digest, research and the page all agree:

- a conversation is an *emailed* pitch. An entry marked Pitched by hand has nobody waiting on a
  reply, so it doesn't take up a slot;
- a pitch nobody answered within `quiet_after_days` is over in practice and stops counting. A
  conversation where the curator spoke last never goes quiet -- it's waiting on you, however
  long it sits there.

Nothing here is stored: it's all derived from the messages, so a reply arriving overnight
changes the answer without anything having to be updated in the right order.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import EmailMessage, MailDirection, Outreach, Profile


@dataclass(frozen=True)
class ConversationLoad:
    """What one profile's conversation load allows tonight."""

    open_now: int
    limit: int
    target: int

    @property
    def allowance(self) -> int:
        """How many new pitches tonight may hand over: room under the ceiling, capped by the target."""
        return max(0, min(self.target, self.limit - self.open_now))

    @property
    def full(self) -> bool:
        return self.allowance == 0


def open_conversations(session: Session, profile: Profile, *, now: datetime) -> int:
    """How many of this profile's emailed pitches are still live."""
    return _open_counts(session, [profile.id], now=now).get(profile.id, 0)


def conversation_load(session: Session, profile: Profile, *, now: datetime) -> ConversationLoad:
    return loads_for(session, [profile], now=now)[profile.id]


def loads_for(session: Session, profiles: Sequence[Profile], *, now: datetime) -> dict[int, ConversationLoad]:
    """Every profile's load, reading the messages once. Profiles with no mail still get an answer."""
    counts = _open_counts(session, [profile.id for profile in profiles], now=now)
    return {
        profile.id: ConversationLoad(
            open_now=counts.get(profile.id, 0),
            limit=profile.open_conversation_limit,
            target=profile.digest_target,
        )
        for profile in profiles
    }


def _open_counts(session: Session, profile_ids: Sequence[int], *, now: datetime) -> dict[int, int]:
    """Open conversations per profile, from the last message on each emailed entry.

    One query: the newest message on every entry of these profiles, with the profile's own quiet
    window applied in Python -- the window differs per profile, and there are tens of rows, not
    millions.
    """
    if not profile_ids:
        return {}
    rows = session.execute(
        select(
            Outreach.profile_id,
            Profile.quiet_after_days,
            EmailMessage.direction,
            EmailMessage.sent_at,
        )
        .join(Outreach, Outreach.id == EmailMessage.outreach_id)
        .join(Profile, Profile.id == Outreach.profile_id)
        .where(Outreach.profile_id.in_(profile_ids))
        .order_by(EmailMessage.outreach_id, EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .distinct(EmailMessage.outreach_id)
    )
    counts: dict[int, int] = {}
    for profile_id, quiet_after_days, direction, sent_at in rows:
        if _is_open(direction, sent_at, quiet_after_days, now):
            counts[profile_id] = counts.get(profile_id, 0) + 1
    return counts


def _is_open(direction: str, sent_at: datetime, quiet_after_days: int, now: datetime) -> bool:
    if direction == MailDirection.IN:
        return True  # they spoke last: waiting on you, however old
    return now - sent_at <= timedelta(days=quiet_after_days)
