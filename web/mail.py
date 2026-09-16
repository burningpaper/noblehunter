"""The Pitch mailbox card, and the Google consent flow that fills it.

Connecting a Gmail mailbox is separate from signing in: it asks for the two Gmail scopes rather
than a name and an email, so it has its own consent round trip. The profile whose card started
it, and a random state, are kept in the session, and the callback refuses anything else.

The card and all three actions are ordinary profile routes: `require_profile` first, so another
artist's card answers 404 like everything else.
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Protocol
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from core.access import NotVisible, is_storable_id, require_profile
from core.gmail import ACCESS_TOKEN_URL, Gmail
from core.mail_crypto import MailNotConfigured, mail_cipher
from core.mailboxes import MailboxProblem, artist_mailboxes, attach_mailbox, connect_mailbox, detach_mailbox
from core.models import MailAccount, Profile
from web.access import CurrentViewer
from web.db import get_db
from web.forms import form_id
from web.settings import WebSettings
from web.templating import templates

logger = logging.getLogger("noble_hunter.mail")

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]

TOKEN_TIMEOUT_SECONDS = 30
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)
SESSION_MAIL_CONNECT = "mail_connect"
NO_REFRESH_TOKEN = "Google didn't send a refresh token, so there would be nothing to store."


class MailExchange(Protocol):
    """Swaps Google's one-time code for a refresh token, the address and a starting history id."""

    def exchange(self, code: str) -> tuple[str, str, str]: ...


class GoogleMailExchange:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def for_redirect(self, redirect_uri: str) -> "GoogleMailExchange":
        """A copy that exchanges codes issued for `redirect_uri`.

        A copy, not a mutation: one of these lives on `app.state` and is shared by every request,
        and Google refuses a code presented with a different redirect URI than it was issued for.
        """
        return GoogleMailExchange(self.client_id, self.client_secret, redirect_uri)

    def exchange(self, code: str) -> tuple[str, str, str]:
        with httpx.Client(timeout=TOKEN_TIMEOUT_SECONDS) as client:
            response = client.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )
            try:
                payload = response.json() if response.status_code == 200 else {}
            except ValueError:
                payload = {}  # an HTML error page, not the token we asked for
            refresh_token = str(payload.get("refresh_token") or "")
            if not refresh_token:
                # No refresh token means offline access wasn't granted, so there'd be nothing to
                # store: Google only sends one when consent is fresh.
                raise MailboxProblem(NO_REFRESH_TOKEN)
            gmail = Gmail(
                client,
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=refresh_token,
            )
            address, history_id = gmail.profile()
        return refresh_token, address, history_id


def google_mail_exchange(settings: WebSettings) -> MailExchange:
    return GoogleMailExchange(settings.google_client_id, settings.google_client_secret.get_secret_value())


@router.get("/profiles/{profile_id}/mail/connect")
def start_connect(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    profile = require_profile(db, viewer, profile_id)
    _cipher_or_404(request)
    state = secrets.token_urlsafe(32)
    request.session[SESSION_MAIL_CONNECT] = {"state": state, "profile_id": profile.id}
    query = urlencode(
        {
            "client_id": request.app.state.settings.google_client_id,
            "redirect_uri": _redirect_uri(request),
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=303)


@router.get("/mail/callback", name="mail_callback")
def finish_connect(
    request: Request,
    db: DbSession,
    viewer: CurrentViewer,
    state: str = "",
    code: str = "",
) -> Response:
    pending = request.session.get(SESSION_MAIL_CONNECT) or {}
    if not state or not secrets.compare_digest(state, str(pending.get("state", ""))):
        raise NotVisible("No mailbox connection is waiting")
    request.session.pop(SESSION_MAIL_CONNECT, None)
    profile = require_profile(db, viewer, int(pending.get("profile_id", 0)))
    cipher = _cipher_or_404(request)

    exchange = request.app.state.mail_exchange
    if isinstance(exchange, GoogleMailExchange):
        exchange = exchange.for_redirect(_redirect_uri(request))
    try:
        refresh_token, address, history_id = exchange.exchange(code)
    except Exception:
        # Deliberately broad: the exchange can fail through httpx, Google's payload, or our own
        # guard, and the person needs the same sentence either way. Logged so it isn't silent.
        logger.warning("Connecting a mailbox failed at the token exchange", exc_info=True)
        return _card(request, db, profile, failed=True)

    mailbox = connect_mailbox(
        db,
        artist_id=profile.artist_id,
        address=address,
        refresh_token=refresh_token,
        cipher=cipher,
        history_id=history_id,
        connected_by=viewer.email,
        now=datetime.now(UTC),
    )
    attach_mailbox(db, profile, mailbox)
    db.commit()
    return RedirectResponse(f"/profiles/{profile.id}", status_code=303)


@router.post("/profiles/{profile_id}/mail/attach")
def attach(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    mail_account_id: FormText = "",
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    chosen = form_id(mail_account_id)
    mailbox = db.get(MailAccount, chosen) if is_storable_id(chosen) else None
    if mailbox is None or mailbox.artist_id != profile.artist_id:
        raise NotVisible(f"Mailbox {chosen} not found")
    try:
        attach_mailbox(db, profile, mailbox)
        db.commit()
    except MailboxProblem as problem:
        return _card(request, db, profile, notice=str(problem))
    return _card(request, db, profile, notice=f"Pitching from {mailbox.address}.")


@router.post("/profiles/{profile_id}/mail/disconnect")
def disconnect(request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer) -> Response:
    profile = require_profile(db, viewer, profile_id)
    detach_mailbox(db, profile, now=datetime.now(UTC))
    db.commit()
    return _card(request, db, profile, notice="This profile isn't pitching from a mailbox now.")


def card_context(
    request: Request,
    db: Session,
    profile: Profile,
    notice: str | None = None,
    failed: bool = False,
) -> dict:
    """What the Pitch mailbox card needs. Used here and by the profile page."""
    configured = cipher_or_none(request) is not None
    mailbox = db.get(MailAccount, profile.mail_account_id) if profile.mail_account_id else None
    others = [box for box in artist_mailboxes(db, profile.artist_id) if box.id != profile.mail_account_id]
    return {
        "profile": profile,
        "mail_configured": configured,
        "mailbox": mailbox,
        "other_mailboxes": others,
        "mail_notice": notice,
        # The sentence itself lives in the template: Jinja escapes what it interpolates, and an
        # apostrophe rendered as &#39; is not what a person should read.
        "mail_failed": failed,
    }


def _card(
    request: Request, db: Session, profile: Profile, notice: str | None = None, failed: bool = False
) -> Response:
    context = card_context(request, db, profile, notice, failed)
    return templates.TemplateResponse(request, "mail/_card.html", context)


def cipher_or_none(request: Request):
    """This app's mail cipher, or None when MAIL_TOKEN_KEY isn't set.

    Shared rather than private: sending a pitch and reading replies both need to know whether
    this deployment can decrypt a stored token at all, and they must agree with the card.
    """
    key = getattr(request.app.state.settings, "mail_token_key", None)
    try:
        return mail_cipher(key.get_secret_value() if key is not None else None)
    except MailNotConfigured:
        return None


def _cipher_or_404(request: Request):
    cipher = cipher_or_none(request)
    if cipher is None:
        raise NotVisible("Mail isn't configured")
    return cipher


def _redirect_uri(request: Request) -> str:
    uri = request.url_for("mail_callback")
    if request.app.state.settings.secure_cookies:
        uri = uri.replace(scheme="https")  # behind Vercel's proxy the app sees http
    return str(uri)
