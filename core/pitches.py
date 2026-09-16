"""A pitch: who it goes to, the draft, sending it, and the thread it becomes.

Email pitching sits on top of the digest rather than beside it. Sending records the `pitched`
verdict through `core.exclusion.record_verdict`, so an emailed pitch and a hand-recorded one
have exactly the same effect on the 90-day rule -- there is one way a curator gets used up, not
two. The entry keeps the Gmail thread id, so a reply found later in the mailbox lands back on
the entry it belongs to, and our own follow-ups stay in the same thread.

Nothing is written until Gmail has accepted the message. If the send fails the entry is
untouched, and Jarred still has his draft.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.contact_routes import USABLE_GRADES, best_contact
from core.contacts import normalize_email
from core.exclusion import PERMANENT_VERDICTS, record_verdict
from core.mime import build_message
from core.models import (
    Contact,
    EmailMessage,
    MailAccount,
    MailDirection,
    Outreach,
    OutreachStatus,
    RouteType,
)

MAX_SUBJECT_LENGTH = 200
NO_EMAIL = "This curator has no email address good enough to write to."
UNUSABLE_EMAIL = "The address on file for this curator isn't a usable email address."
# Sending is already done once the status reaches any of these; a follow-up must not walk the
# entry backwards to "pitched" (a reply came in, or the track was placed).
AT_OR_PAST_PITCHED = (OutreachStatus.PITCHED, OutreachStatus.REPLIED, OutreachStatus.PLACED)


class PitchProblem(ValueError):
    """Something a person can fix: no address, an empty draft."""


def pitch_address(session: Session, outreach: Outreach) -> str:
    """The curator's best email address, or raise.

    `best_contact` picks by the same rule the digest page shows, but it can pick an Instagram
    handle; email pitching needs an email, so the curator's best *email* is used instead.

    Same grades as everywhere else, though. A grade-C address is one where only the name
    matched -- nobody corroborated it as this curator's -- and the rest of the app won't use it,
    so neither will this. Writing to it would put a real email in a stranger's inbox.
    """
    best = best_contact(session, outreach.curator_id)
    if best is not None and best.route_type == RouteType.EMAIL:
        return _as_email(best.value)
    fallback = session.scalar(
        select(Contact)
        .where(
            Contact.curator_id == outreach.curator_id,
            Contact.route_type == RouteType.EMAIL,
            Contact.confidence.in_(USABLE_GRADES),
        )
        .order_by(Contact.confidence, Contact.id)
        .limit(1)
    )
    if fallback is None:
        raise PitchProblem(NO_EMAIL)
    return _as_email(fallback.value)


def _as_email(stored: str) -> str:
    """A stored contact turned into an address fit for a To header, or raise.

    Research keeps `value` exactly as it was scraped and normalizes only `contact_key`, so a
    perfectly good contact can sit in the database as `<Nina@B.com>`, `nina@b.com.` or
    `mailto:nina@b.com`. Sent as-is that bounces, or renders as nonsense in the curator's mail
    client. `normalize_email` is the very function that decided this address *is* this curator
    -- using it here is what keeps the address we write to and the identity we deduplicate on
    from disagreeing.

    A value that won't parse at all shouldn't be reachable: research derives the key from the
    value and drops the contact when that fails. The guard is here because nothing keeps the
    two agreeing afterwards, and the wrong answer -- putting an unparseable string in a To
    header -- is a real email going somewhere nobody chose.

    The deliberate trade-off: this lowercases the whole address, local part included, which
    RFC-wise is lossy, since a local part may in theory be case-sensitive. Every mainstream
    provider treats it as case-insensitive, and the app already settles curator identity this
    way, so agreeing with it beats inventing a second rule. Don't "fix" this to preserve the
    local part's case without changing `contact_key` to match, or the two drift apart again.
    """
    address = normalize_email(stored or "")
    if address is None:
        raise PitchProblem(UNUSABLE_EMAIL)
    return address


def save_draft(session: Session, outreach: Outreach, *, subject: str, body: str, now: datetime) -> None:
    """Keep what's in the box, so a closed tab doesn't lose it."""
    outreach.draft_subject, outreach.draft_body = _clean(subject, body)
    outreach.draft_updated_at = now
    session.flush()


def send_pitch(
    session: Session,
    outreach: Outreach,
    *,
    gmail,
    mailbox: MailAccount,
    subject: str,
    body: str,
    now: datetime,
    send_key: str | None = None,
) -> EmailMessage:
    """Send the pitch from `mailbox` and record it. Raises before writing anything if it can't.

    `send_key` comes from the panel that posted the form: the same key twice is a double-click
    or a resubmitted page, and sends nothing the second time.
    """
    clean_subject, clean_body = _clean(subject, body)
    if send_key and _already_sent(session, send_key):
        raise PitchProblem("That pitch has already been sent.")
    _refuse_if_ruled_out(outreach)
    to_address = pitch_address(session, outreach)
    raw = build_message(
        from_address=mailbox.address,
        to_address=to_address,
        subject=clean_subject,
        body=clean_body,
        in_reply_to=_last_incoming_id(session, outreach),
    )
    message_id, thread_id = gmail.send(raw, thread_id=outreach.gmail_thread_id)

    record = EmailMessage(
        outreach_id=outreach.id,
        mail_account_id=mailbox.id,
        direction=MailDirection.OUT,
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        from_address=mailbox.address,
        to_address=to_address,
        subject=clean_subject,
        body_text=clean_body,
        sent_at=now,
        send_key=send_key,
    )
    session.add(record)
    outreach.gmail_thread_id = thread_id
    outreach.mail_account_id = mailbox.id
    outreach.draft_subject = None
    outreach.draft_body = None
    outreach.draft_updated_at = None
    if outreach.status not in AT_OR_PAST_PITCHED:
        record_verdict(session, outreach.id, OutreachStatus.PITCHED, now)
    session.flush()
    return record


def thread_messages(session: Session, outreach: Outreach) -> list[EmailMessage]:
    """Everything sent and received on this entry, oldest first."""
    return list(
        session.scalars(
            select(EmailMessage)
            .where(EmailMessage.outreach_id == outreach.id)
            .order_by(EmailMessage.sent_at, EmailMessage.id)
        )
    )


def mark_thread_read(session: Session, outreach: Outreach, *, now: datetime) -> int:
    """Their unread messages on this entry have now been seen. Returns how many were marked.

    Only incoming messages are ever unread -- our own were read as we wrote them -- and `read_at`
    feeds nothing but the Inbox's unread count. Whether a conversation is *open* is worked out
    from the messages themselves, so a missed call here can never strand an entry.
    """
    unread = list(
        session.scalars(
            select(EmailMessage).where(
                EmailMessage.outreach_id == outreach.id,
                EmailMessage.direction == MailDirection.IN,
                EmailMessage.read_at.is_(None),
            )
        )
    )
    for message in unread:
        message.read_at = now
    session.flush()
    return len(unread)


def _refuse_if_ruled_out(outreach: Outreach) -> None:
    """A curator marked bad-fit or dead is never written to, and their entry is never downgraded.

    The verdict buttons and this module can both change an entry, so the rule lives here rather
    than in the route: whatever order the two happen in, a ruled-out curator stays ruled out.
    """
    if outreach.curator.excluded_at is not None or outreach.status in PERMANENT_VERDICTS:
        raise PitchProblem("You ruled this curator out, so nothing was sent.")


def _clean(subject: str, body: str) -> tuple[str, str]:
    clean_subject = " ".join(str(subject or "").split())[:MAX_SUBJECT_LENGTH]
    clean_body = str(body or "").strip()
    if not clean_subject:
        raise PitchProblem("Give the email a subject.")
    if not clean_body:
        raise PitchProblem("Write something before saving or sending.")
    return clean_subject, clean_body


def _already_sent(session: Session, send_key: str) -> bool:
    return session.scalar(select(EmailMessage.id).where(EmailMessage.send_key == send_key)) is not None


def _last_incoming_id(session: Session, outreach: Outreach) -> str | None:
    """The curator's last Message-ID, so our reply lands in their thread, not a new one."""
    return session.scalar(
        select(EmailMessage.rfc822_message_id)
        .where(
            EmailMessage.outreach_id == outreach.id,
            EmailMessage.direction == MailDirection.IN,
            EmailMessage.rfc822_message_id.is_not(None),
        )
        .order_by(EmailMessage.sent_at.desc(), EmailMessage.id.desc())
        .limit(1)
    )
