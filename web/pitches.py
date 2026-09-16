"""Writing a pitch on a digest entry: read the panel, ask Claude, save the draft, send it.

The panel is part of the digest entry but loads on its own, the first time the disclosure is
opened, so the digest page stays one query per night. Every route starts with `require_outreach`,
so another artist's entry is not found before anything else happens.

Sending is the only route here that touches the outside world. It needs three things -- a
mailbox on the profile, an email address for the curator, and a draft -- and says plainly which
one is missing rather than failing. A `send_key` rendered into the form makes a double-click or
a resubmitted page harmless.
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.access import Viewer, require_outreach
from core.digest_view import entry_view
from core.gmail import GmailError
from core.mail_crypto import MailNotConfigured
from core.mailboxes import MailboxProblem, mark_needs_reconnect
from core.mime import HeaderProblem
from core.models import MailAccount, Outreach, ProfileTrack, User
from core.pitch_writer import PitchRequest, PitchWriterError
from core.pitches import (
    PitchProblem,
    mark_thread_read,
    pitch_address,
    save_draft,
    send_pitch,
    thread_messages,
)
from web.access import CurrentViewer
from web.db import get_db
from web.digest import VERDICT_LABELS
from web.forms import form_id
from web.mail import cipher_or_none
from web.templating import templates

logger = logging.getLogger("noble_hunter.pitches")

router = APIRouter(prefix="/outreach/{outreach_id}/pitch")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]

GMAIL_TIMEOUT_SECONDS = 30
BAD_ADDRESS = (
    "An address on file isn't a usable email address, so nothing was sent. Check the mailbox on "
    "the profile page and the curator's contact on the card."
)
NO_MAILBOX = "This profile isn't pitching from a mailbox yet. Connect one on the profile page."
NO_WRITER = "Drafting with Claude needs ANTHROPIC_API_KEY. You can still write the pitch yourself."
RECONNECT = "Google refused this mailbox. Reconnect it on the profile page, then send again."
SENT = "Sent."


@router.get("")
def panel(request: Request, outreach_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    # Opening the thread is what "reading" means; this is the only place `read_at` is set.
    if mark_thread_read(db, outreach, now=datetime.now(UTC)):
        db.commit()
    return _panel(request, db, outreach)


@router.post("/write")
def write(
    request: Request,
    outreach_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    instruction: FormText = "",
    subject: FormText = "",
    body: FormText = "",
    track_id: FormText = "",
) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    writer = request.app.state.pitch_writer
    if writer is None:
        return _panel(request, db, outreach, subject=subject, body=body, error=NO_WRITER)

    chosen_track = _track(outreach, form_id(track_id))
    try:
        pitch = writer.write(_pitch_request(db, outreach, instruction, body, chosen_track, viewer))
    except PitchWriterError as error:
        return _panel(request, db, outreach, subject=subject, body=body, error=str(error))

    outreach.draft_track_id = chosen_track.id if chosen_track else None
    save_draft(db, outreach, subject=pitch.subject, body=pitch.body, now=datetime.now(UTC))
    db.commit()
    return _panel(request, db, outreach, notice="Draft written. Edit it before sending.")


@router.post("/save")
def save(
    request: Request,
    outreach_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    subject: FormText = "",
    body: FormText = "",
    track_id: FormText = "",
) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    try:
        outreach.draft_track_id = _track_id_or_none(outreach, track_id)
        save_draft(db, outreach, subject=subject, body=body, now=datetime.now(UTC))
        db.commit()
    except PitchProblem as problem:
        db.rollback()
        return _panel(request, db, outreach, subject=subject, body=body, error=str(problem), status_code=422)
    return _panel(request, db, outreach, notice="Draft saved")


@router.post("/send")
def send(
    request: Request,
    outreach_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    subject: FormText = "",
    body: FormText = "",
    send_key: FormText = "",
    track_id: FormText = "",
) -> Response:
    outreach = require_outreach(db, viewer, outreach_id)
    mailbox = _mailbox(db, outreach)
    cipher = cipher_or_none(request)
    if mailbox is None or cipher is None:
        return _panel(request, db, outreach, subject=subject, body=body, error=NO_MAILBOX, status_code=422)

    now = datetime.now(UTC)
    try:
        outreach.draft_track_id = _track_id_or_none(outreach, track_id)
        with httpx.Client(timeout=GMAIL_TIMEOUT_SECONDS) as http:
            gmail = request.app.state.gmail_for(mailbox, cipher, request.app.state.settings, http)
            send_pitch(
                db,
                outreach,
                gmail=gmail,
                mailbox=mailbox,
                subject=subject,
                body=body,
                now=now,
                send_key=send_key.strip() or None,
            )
        db.commit()
    except MailNotConfigured:
        # The key can't read this mailbox's stored token (it was rotated, or the row was
        # altered). Reconnecting stores a fresh one, so that's what to say -- and a 500 is
        # exactly what this must not be.
        db.rollback()
        logger.warning("A stored Gmail token couldn't be read")
        return _panel(request, db, outreach, subject=subject, body=body, error=RECONNECT)
    except IntegrityError:
        # The send-key check is read-then-write, so two genuinely concurrent posts both pass it
        # and the unique index catches the loser. Same meaning as the check: it went once.
        db.rollback()
        return _panel(request, db, outreach, notice=SENT)
    except (PitchProblem, MailboxProblem) as problem:
        db.rollback()
        already = "already been sent" in str(problem)
        return _panel(
            request,
            db,
            outreach,
            subject="" if already else subject,
            body="" if already else body,
            error=None if already else str(problem),
            notice=SENT if already else None,
            status_code=200 if already else 422,
        )
    except HeaderProblem:
        # A field that can't be a header -- in practice a mailbox stored with no address at all.
        # Nothing caught this, so it left here as a 500. This route's whole promise is to say
        # which of the three things it needs is wrong, and an unusable address is one of them.
        # The message is deliberately vague about which end: both are "an address on file".
        db.rollback()
        logger.warning("A pitch couldn't be built into a message")
        return _panel(request, db, outreach, subject=subject, body=body, error=BAD_ADDRESS, status_code=422)
    except GmailError as error:
        db.rollback()
        if error.kind == "auth":
            mark_needs_reconnect(db, mailbox, str(error))
            db.commit()
        logger.warning("Sending a pitch failed (%s)", error.kind)
        message = RECONNECT if error.kind == "auth" else "Gmail couldn't send that. Try again shortly."
        return _panel(request, db, outreach, subject=subject, body=body, error=message)
    return _panel(request, db, outreach, notice=SENT, swap_status=True)


def _panel(
    request: Request,
    db: Session,
    outreach: Outreach,
    *,
    subject: str | None = None,
    body: str | None = None,
    notice: str | None = None,
    error: str | None = None,
    status_code: int = 200,
    swap_status: bool = False,
) -> Response:
    """Render the panel. `subject`/`body` override the saved draft, so a refused post keeps typing."""
    mailbox = _mailbox(db, outreach)
    try:
        address, address_error = pitch_address(db, outreach), None
    except PitchProblem as problem:
        address, address_error = None, str(problem)

    profile = outreach.profile
    context = {
        "outreach": outreach,
        "profile": profile,
        "playlist_name": outreach.playlist.name,
        "curator_name": outreach.curator.display_name,
        "address": address,
        "address_error": address_error,
        "mailbox": mailbox,
        "mail_configured": cipher_or_none(request) is not None,
        "can_write": request.app.state.pitch_writer is not None,
        "no_writer_note": None if request.app.state.pitch_writer is not None else NO_WRITER,
        "subject": outreach.draft_subject or "" if subject is None else subject,
        "body": outreach.draft_body or "" if body is None else body,
        "tracks": sorted(profile.tracks, key=lambda track: track.title.casefold()),
        "draft_track_id": outreach.draft_track_id,
        "messages": thread_messages(db, outreach),
        "send_key": secrets.token_hex(16),
        "notice": notice,
        "error": error,
        "swap_status": swap_status,
        # The entry's own status, not the word "Pitched": a follow-up sent on an entry already
        # at `replied` or `placed` must not swap the card's tag backwards.
        "status_label": VERDICT_LABELS.get(outreach.status, outreach.status),
        "summary_label": _summary_label(db, outreach) if swap_status else "",
    }
    return templates.TemplateResponse(request, "pitch/_panel.html", context, status_code=status_code)


def _summary_label(db: Session, outreach: Outreach) -> str:
    """What the card's disclosure should say now, read from the same state the digest reads.

    Derived rather than hardcoded so the card and the digest page can't drift: `entry_view`
    gives back the entry's `EntryMail`, and the wording here matches `digest/_entry.html`.
    """
    mail = entry_view(db, outreach.id, today=datetime.now(UTC).date()).mail
    if mail.waiting_on_you:
        return "Read the reply"
    return "The conversation" if mail.started else "Write a pitch"


def _mailbox(db: Session, outreach: Outreach) -> MailAccount | None:
    """The mailbox this entry pitches from: the one it already used, else the profile's."""
    mail_account_id = outreach.mail_account_id or outreach.profile.mail_account_id
    mailbox = db.get(MailAccount, mail_account_id) if mail_account_id else None
    return mailbox if mailbox is not None and mailbox.is_connected else None


def _track(outreach: Outreach, track_id: int) -> ProfileTrack | None:
    """The track this pitch is about: the one chosen, else the entry's saved choice, else the first."""
    tracks = {track.id: track for track in outreach.profile.tracks}
    chosen = tracks.get(track_id) or tracks.get(outreach.draft_track_id or 0)
    if chosen is not None:
        return chosen
    return min(tracks.values(), key=lambda track: track.title.casefold(), default=None)


def _track_id_or_none(outreach: Outreach, raw: str) -> int | None:
    track = _track(outreach, form_id(raw))
    return track.id if track is not None else None


def _sender_name(db: Session, viewer: Viewer) -> str | None:
    """The name the signed-in person signs the letter with, or nothing at all.

    Never the local part of their email. "burningpaper" is an address, not a name, and it used
    to sign every draft -- including the plain fallback used when Claude has already failed,
    which is the least supervised text this feature can put in front of a curator. Nothing is
    the better answer: `pitch_writer` then signs as the artist instead, which is a true thing
    for a musician's cold pitch to say. `users.name` comes from Google at sign-in and may be
    null, so `None` is a normal result, not an error. `viewer.email` is already lowercased to
    match the column.
    """
    return db.scalar(select(User.name).where(User.email == viewer.email))


def _pitch_request(
    db: Session, outreach: Outreach, instruction: str, body: str, track: ProfileTrack | None, viewer: Viewer
) -> PitchRequest:
    profile = outreach.profile
    tracks = ((track.title, track.description or ""),) if track else ()
    return PitchRequest(
        artist_name=profile.artist.name,
        profile_name=profile.name,
        curator_name=outreach.curator.display_name,
        playlist_name=outreach.playlist.name,
        playlist_url=f"https://open.spotify.com/playlist/{outreach.playlist_id}",
        brief=outreach.brief_text or "",
        angle=outreach.suggested_angle,  # typed `str | None`, so this one may stay as it is
        reference_artists=tuple(artist.display_name for artist in profile.reference_artists),
        tracks=tracks,
        sender_name=_sender_name(db, viewer),
        instruction=instruction,
        previous_body=body,
    )
