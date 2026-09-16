"""Building emailed conversations in tests: an entry with messages on it.

Shared by tests/test_conversations.py and the allowance tests in test_digest.py and
test_research.py, so all three agree about what a conversation looks like.
"""

from datetime import datetime, timedelta

from core.models import EmailMessage, MailAccount, MailDirection
from tests.factories import make_curator, make_mailbox, make_outreach, make_playlist


def pitched(session, profile, *, messages, now: datetime):
    """An outreach entry with `messages`, each given as (direction, days before `now`).

    The entry's digest date comes from `now`, so this suits any test's clock -- the digest and
    research suites each have their own, and a conversation dated in their future would be odd.
    """
    mailbox_id = profile.mail_account_id or _a_mailbox(session, profile).id
    curator = make_curator(session)
    outreach = make_outreach(
        session, curator, profile, now.date(), playlist=make_playlist(session, curator=curator)
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
