"""Reading a mailbox for replies, and putting each one on the entry it belongs to.

The mailbox is the artist's own Gmail, so most of what arrives in it is none of Noble Hunter's
business. Only a message whose Gmail thread matches an outreach entry on *this* mailbox is
fetched and stored; everything else is counted and forgotten, never read into the database.

Gmail's history API answers "what changed since history id X", which is cheap enough to ask
every few minutes. A history id older than about a week expires; that isn't a failure, it just
means re-reading the threads we already know about and taking a fresh id.

Nothing here raises for an ordinary problem: each mailbox returns a `SyncOutcome`, so one
mailbox that needs reconnecting can't stop the others being read.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.gmail import GmailError
from core.mailboxes import artist_mailboxes, mark_needs_reconnect
from core.mime import parse_message
from core.models import EmailMessage, MailAccount, MailDirection, Outreach

logger = logging.getLogger("noble_hunter.mail_sync")

RECONNECT_MESSAGE = "Google refused this mailbox's access. Connect it again to read replies."


@dataclass
class SyncOutcome:
    stored: int = 0
    ignored: int = 0
    reset: bool = False
    error: str | None = None


def sync_mailbox(session: Session, mailbox: MailAccount, gmail, *, now: datetime) -> SyncOutcome:
    """Bring this mailbox up to date. Returns what happened; never raises for an ordinary problem."""
    outcome = SyncOutcome()
    try:
        added, new_history_id = _changes(session, gmail, mailbox, outcome)
    except GmailError as error:
        return _failed(session, mailbox, error, outcome)

    threads = _threads_we_know(session, mailbox)
    for message_id, thread_id in added:
        outreach_id = threads.get(thread_id)
        if outreach_id is None:
            outcome.ignored += 1  # someone else's mail in the same mailbox
            continue
        if _already_stored(session, mailbox.id, message_id):
            continue
        try:
            payload = gmail.message(message_id)
            _store(session, mailbox, outreach_id, payload)
        except GmailError as error:
            if error.kind in ("auth", "config"):
                # Neither gets better on the next message: the token is refused, or the project
                # is. Carrying on would count the whole mailbox as "ignored" and report a clean
                # round, which is how a broken setup stays invisible.
                return _failed(session, mailbox, error, outcome)
            logger.warning("Couldn't read message %s: %s", message_id, error)
            outcome.ignored += 1
            continue
        except (KeyError, TypeError, ValueError):
            logger.warning("Message %s couldn't be read; skipping", message_id, exc_info=True)
            outcome.ignored += 1
            continue
        outcome.stored += 1

    mailbox.history_id = new_history_id
    mailbox.last_checked_at = now
    # Not `if needs_reconnect`: a config failure records `last_error` without raising the flag,
    # and a stale sentence about a Google project that has since been fixed shouldn't outlive it.
    if mailbox.needs_reconnect or mailbox.last_error:
        mailbox.needs_reconnect, mailbox.last_error = False, None
    session.flush()
    return outcome


def sync_all(
    session: Session,
    *,
    open_gmail: Callable[[MailAccount], object],
    now: datetime,
) -> dict[int, SyncOutcome]:
    """Sync every connected mailbox, one at a time. One failing doesn't stop the rest."""
    outcomes: dict[int, SyncOutcome] = {}
    for mailbox in _connected_mailboxes(session):
        try:
            outcomes[mailbox.id] = sync_mailbox(session, mailbox, open_gmail(mailbox), now=now)
        except Exception as error:  # opening the mailbox itself failed (no token, no network)
            logger.warning("Couldn't read %s: %s", mailbox.address, type(error).__name__)
            outcomes[mailbox.id] = SyncOutcome(error=f"{type(error).__name__}: {error}")
    return outcomes


def _changes(
    session: Session, gmail, mailbox: MailAccount, outcome: SyncOutcome
) -> tuple[list[tuple[str, str]], str]:
    """What's new since our history id -- or, if that id has expired, every thread we know about."""
    if mailbox.history_id:
        try:
            return gmail.history_since(mailbox.history_id)
        except GmailError as error:
            if error.kind != "history-gone":
                raise
            logger.info("History id for %s has expired; re-reading known threads", mailbox.address)

    # No history to work from: read the threads we started, and take a fresh id to carry on from.
    outcome.reset = True
    _address, history_id = gmail.profile()
    found: list[tuple[str, str]] = []
    for thread_id in _threads_we_know(session, mailbox):
        thread = gmail.thread(thread_id)
        found += [(str(message["id"]), thread_id) for message in thread.get("messages", [])]
    return found, history_id


def _threads_we_know(session: Session, mailbox: MailAccount) -> dict[str, int]:
    """Gmail thread id -> outreach id, for entries pitched from this mailbox."""
    rows = session.execute(
        select(Outreach.gmail_thread_id, Outreach.id).where(
            Outreach.mail_account_id == mailbox.id, Outreach.gmail_thread_id.is_not(None)
        )
    )
    return {thread_id: outreach_id for thread_id, outreach_id in rows}


def _already_stored(session: Session, mail_account_id: int, gmail_message_id: str) -> bool:
    return (
        session.scalar(
            select(EmailMessage.id).where(
                EmailMessage.mail_account_id == mail_account_id,
                EmailMessage.gmail_message_id == gmail_message_id,
            )
        )
        is not None
    )


def _store(session: Session, mailbox: MailAccount, outreach_id: int, payload: dict) -> None:
    # `parse_message` is deliberately forgiving, so a malformed payload comes back with an empty
    # id and a 1970 date rather than raising. Storing that would put a message in the thread that
    # sorts before everything and never goes quiet, so refuse it here and let the caller skip it.
    if not payload.get("id") or not payload.get("internalDate"):
        raise ValueError("a message with no id or no date can't be placed in a thread")
    parsed = parse_message(payload)
    ours = parsed.from_address.strip().lower() == mailbox.address.strip().lower()
    session.add(
        EmailMessage(
            mail_account_id=mailbox.id,
            outreach_id=outreach_id,
            gmail_message_id=parsed.gmail_message_id,
            gmail_thread_id=parsed.gmail_thread_id,
            rfc822_message_id=parsed.message_id_header,
            direction=MailDirection.OUT if ours else MailDirection.IN,
            from_address=parsed.from_address,
            to_address=parsed.to_address,
            subject=parsed.subject,
            body_text=parsed.body_text,
            quoted_text=parsed.quoted_text,
            sent_at=parsed.sent_at,
        )
    )
    session.flush()


def _failed(session: Session, mailbox: MailAccount, error: GmailError, outcome: SyncOutcome) -> SyncOutcome:
    """Record what went wrong. Auth failures need a person; anything else will be tried again."""
    if error.kind == "auth":
        mark_needs_reconnect(session, mailbox, RECONNECT_MESSAGE)
    elif error.kind == "config":
        # The app's Google project is wrong, not this mailbox. Keep what Gmail said on the row so
        # it isn't only in a log line, but leave `needs_reconnect` alone: nothing clears that flag
        # but a person completing Google's consent flow, and consent can't switch an API on.
        mailbox.last_error = str(error)
        session.flush()
    outcome.error = str(error)
    return outcome


def _connected_mailboxes(session: Session) -> list[MailAccount]:
    """Every mailbox worth reading. One already needing reconnection is skipped: asking Google
    again would only fail the same way, and the person has already been told."""
    artist_ids = list(session.scalars(select(MailAccount.artist_id).distinct()))
    return [
        mailbox
        for artist_id in artist_ids
        for mailbox in artist_mailboxes(session, artist_id)
        if not mailbox.needs_reconnect
    ]
