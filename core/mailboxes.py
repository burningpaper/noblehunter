"""An artist's Gmail mailboxes: connecting one, sharing it between profiles, and letting go.

A mailbox belongs to an artist, not to a profile, so two profiles for the same artist can pitch
from one Gmail. The database enforces that with a composite foreign key (migration 0007); this
module refuses it earlier, with a message a person can read.

Connecting the same address twice refreshes the row rather than making a second one: Google
issues a new refresh token each time someone goes through consent, and the old one may already
have been revoked. Letting go only hands the mailbox back once no profile uses it, because the
caller then revokes it at Google; until then its threads must keep working.
"""

from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.mail_crypto import decrypt_token as _decrypt
from core.mail_crypto import encrypt_token
from core.models import MailAccount, Profile


class MailboxProblem(ValueError):
    """Something a person can fix: the wrong artist's mailbox, or one that isn't connected."""


def connect_mailbox(
    session: Session,
    *,
    artist_id: int,
    address: str,
    refresh_token: str,
    cipher: Fernet,
    history_id: str,
    connected_by: str,
    now: datetime,
) -> MailAccount:
    """Store Gmail access for this artist, replacing anything held for the same address."""
    mailbox = session.scalar(
        select(MailAccount).where(
            MailAccount.artist_id == artist_id, func.lower(MailAccount.address) == address.strip().lower()
        )
    )
    if mailbox is None:
        # Stored lowercased, like every other address in the app (users, contacts, members):
        # the unique constraint is on the raw column, so two spellings would otherwise be two
        # mailboxes for one Gmail, and the lookup above would pick between them arbitrarily.
        mailbox = MailAccount(artist_id=artist_id, address=address.strip().lower())
        session.add(mailbox)
    mailbox.refresh_token_encrypted = encrypt_token(cipher, refresh_token)
    mailbox.history_id = history_id
    mailbox.connected_at = now
    mailbox.connected_by = connected_by
    mailbox.disconnected_at = None
    mailbox.needs_reconnect = False
    mailbox.last_error = None
    session.flush()
    return mailbox


def refresh_token_for(mailbox: MailAccount, cipher: Fernet) -> str:
    """The mailbox's refresh token, or raise if it isn't connected any more."""
    if not mailbox.is_connected or mailbox.refresh_token_encrypted is None:
        raise MailboxProblem(f"{mailbox.address} isn't connected any more. Connect it again to use it.")
    return _decrypt(cipher, mailbox.refresh_token_encrypted)


def attach_mailbox(session: Session, profile: Profile, mailbox: MailAccount) -> None:
    """Pitch this profile's mail from `mailbox`."""
    if mailbox.artist_id != profile.artist_id:
        raise MailboxProblem("That mailbox belongs to another artist.")
    if not mailbox.is_connected:
        raise MailboxProblem(f"{mailbox.address} isn't connected any more. Connect it again to use it.")
    profile.mail_account_id = mailbox.id
    session.flush()


def detach_mailbox(session: Session, profile: Profile, *, now: datetime) -> MailAccount | None:
    """Stop pitching this profile from its mailbox.

    Returns the mailbox only when no profile uses it any more, meaning the caller should revoke
    it at Google. Its stored token is cleared here either way in that case; past messages stay.
    """
    mailbox = session.get(MailAccount, profile.mail_account_id) if profile.mail_account_id else None
    profile.mail_account_id = None
    session.flush()
    if mailbox is None or _still_used(session, mailbox.id):
        return None
    mailbox.refresh_token_encrypted = None
    mailbox.disconnected_at = now
    mailbox.needs_reconnect = False
    mailbox.last_error = None  # whatever went wrong last is over; don't keep showing it
    session.flush()
    return mailbox


def mark_needs_reconnect(session: Session, mailbox: MailAccount, message: str) -> None:
    """Google refused this mailbox's access; nothing will work until someone reconnects it."""
    mailbox.needs_reconnect = True
    mailbox.last_error = message
    session.flush()


def artist_mailboxes(session: Session, artist_id: int) -> list[MailAccount]:
    """This artist's connected mailboxes, including any that need reconnecting."""
    return list(
        session.scalars(
            select(MailAccount)
            .where(
                MailAccount.artist_id == artist_id,
                MailAccount.disconnected_at.is_(None),
                MailAccount.refresh_token_encrypted.is_not(None),
            )
            .order_by(func.lower(MailAccount.address), MailAccount.id)
        )
    )


def _still_used(session: Session, mail_account_id: int) -> bool:
    return (
        session.scalar(select(Profile.id).where(Profile.mail_account_id == mail_account_id).limit(1))
        is not None
    )
