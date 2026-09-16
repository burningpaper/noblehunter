"""Building emailed conversations in tests: an entry with messages on it.

Shared by tests/test_conversations.py and the allowance tests in test_digest.py and
test_research.py, so all three agree about what a conversation looks like.
"""

from datetime import datetime, timedelta

from core.models import EmailMessage, MailAccount, MailDirection
from tests.factories import make_curator, make_mailbox, make_outreach, make_playlist


def pitched(session, profile, *, messages, now: datetime):
    """An outreach entry with `messages`, each given as (direction, days before `now`).

    The entry is dated the night its oldest message went out, which is what makes it a
    conversation rather than one of tonight's leads. Dating it `now` would put it in tonight's
    digest, where the digest counts it as a lead already handed over -- so a test asking for
    four open conversations would silently also be asking for four fewer leads tonight.
    """
    mailbox_id = profile.mail_account_id or _a_mailbox(session, profile).id
    curator = make_curator(session)
    pitched_on = (now - timedelta(days=max((days for _direction, days in messages), default=0))).date()
    outreach = make_outreach(
        session, curator, profile, pitched_on, playlist=make_playlist(session, curator=curator)
    )
    outreach.mail_account_id = mailbox_id
    outreach.gmail_thread_id = f"thread{outreach.id}"
    session.flush()
    for index, (direction, days) in enumerate(messages):
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox_id,
                direction=direction,
                gmail_message_id=f"{outreach.id}-{index}",
                gmail_thread_id=outreach.gmail_thread_id,
                from_address="a@b.com",
                to_address="c@d.com",
                subject="Kelvin",
                body_text="…",
                sent_at=now - timedelta(days=days),
            )
        )
    session.flush()
    return outreach


def open_conversation(session, profile, *, now: datetime, days_ago: int = 1):
    """The simple case: one pitch we sent, still within the quiet window."""
    return pitched(session, profile, messages=[(MailDirection.OUT, days_ago)], now=now)


def _a_mailbox(session, profile) -> MailAccount:
    mailbox = make_mailbox(session, profile.artist)
    profile.mail_account_id = mailbox.id
    session.flush()
    return mailbox
