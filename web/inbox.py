"""The Inbox page, and reading the mail on demand.

The runner reads every five minutes, which is right for the background but wrong for the moment
someone is waiting on a reply. "Check now" reads the viewer's own mailboxes there and then. It
is deliberately best-effort: a Vercel function has seconds, so it reads what it can, says what
it found, and leaves the rest to the runner.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.access import Viewer, require_profile, visible_to
from core.inbox_view import inbox_view
from core.mail_sync import sync_mailbox
from core.models import MailAccount
from web.access import CurrentViewer
from web.db import get_db
from web.forms import form_id
from web.mail import cipher_or_none
from web.templating import templates

logger = logging.getLogger("noble_hunter.inbox")

router = APIRouter(prefix="/inbox")
DbSession = Annotated[Session, Depends(get_db)]

CHECK_TIMEOUT_SECONDS = 15
NO_MAILBOX = "There's no mailbox to check yet. Connect one on a profile first."


@router.get("")
def page(request: Request, db: DbSession, viewer: CurrentViewer, profile: str = "") -> Response:
    chosen = form_id(profile)
    if chosen:
        require_profile(db, viewer, chosen)  # another artist's id is not found, as everywhere else
    context = _context(db, viewer, profile_id=chosen or None)
    return templates.TemplateResponse(request, "inbox/page.html", context)


@router.post("/check")
def check_now(request: Request, db: DbSession, viewer: CurrentViewer) -> Response:
    """Read the viewer's own mailboxes now, then re-render the list with whatever arrived."""
    mailboxes = _their_mailboxes(db, viewer)
    cipher = cipher_or_none(request)
    if not mailboxes or cipher is None:
        return _list(request, db, viewer, NO_MAILBOX)

    stored, problems = 0, 0
    now = datetime.now(UTC)
    with httpx.Client(timeout=CHECK_TIMEOUT_SECONDS) as http:
        for mailbox in mailboxes:
            try:
                gmail = request.app.state.gmail_for(mailbox, cipher, request.app.state.settings, http)
                outcome = sync_mailbox(db, mailbox, gmail, now=now)
            except Exception:
                logger.warning("Couldn't read %s from the Inbox", mailbox.address, exc_info=True)
                problems += 1
                continue
            stored += outcome.stored
            problems += 1 if outcome.error else 0
    db.commit()

    notice = f"{stored} new {'reply' if stored == 1 else 'replies'}." if stored else "No new replies."
    if problems:
        notice += " Some mailboxes couldn't be read; the runner will try again."
    return _list(request, db, viewer, notice)


def _list(request: Request, db: Session, viewer: Viewer, notice: str) -> Response:
    """Just the conversations, for the fragment "Check now" swaps in."""
    return templates.TemplateResponse(request, "inbox/_list.html", _context(db, viewer, notice=notice))


def _context(db: Session, viewer: Viewer, notice: str | None = None, profile_id: int | None = None) -> dict:
    view = inbox_view(db, viewer, now=datetime.now(UTC), profile_id=profile_id)
    return {"view": view, "inbox_notice": notice}


def _their_mailboxes(db: Session, viewer: Viewer) -> list[MailAccount]:
    return list(
        db.scalars(
            select(MailAccount).where(
                visible_to(viewer, MailAccount.artist_id),
                MailAccount.disconnected_at.is_(None),
                MailAccount.refresh_token_encrypted.is_not(None),
                MailAccount.needs_reconnect.is_(False),
            )
        )
    )
