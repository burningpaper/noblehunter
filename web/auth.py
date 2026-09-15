"""Sign in with Google.

Authlib does the OAuth work: it keeps state and nonce in the session, verifies the ID token's
signature against Google's published keys, and checks the nonce. Our part is the policy:
only a *verified* email that is an admin (ALLOWED_EMAILS) or a member of an artist gets a
session, `next` can only point back into this site, and the session is rebuilt from scratch
on every sign-in.
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Protocol

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.access import Viewer, record_sign_in, viewer_for
from web.db import get_db
from web.sessions import SESSION_CSRF, SESSION_NEXT, SESSION_USER, current_user, safe_next
from web.settings import WebSettings
from web.templating import templates

logger = logging.getLogger("noble_hunter.web.auth")

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_SCOPES = "openid email profile"


class IdentityProvider(Protocol):
    async def authorize_redirect(self, request: Request, redirect_uri: str) -> Response: ...

    async def authorize_access_token(self, request: Request) -> dict: ...


def google_provider(settings: WebSettings) -> IdentityProvider:
    """Registers Google; the discovery document is fetched lazily on first sign-in."""
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        server_metadata_url=GOOGLE_METADATA_URL,
        client_kwargs={"scope": GOOGLE_SCOPES},
    )
    return oauth.google


router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/login", include_in_schema=False)
def login(request: Request, next: str | None = None) -> Response:
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    if next is not None:
        request.session[SESSION_NEXT] = safe_next(next)
    return templates.TemplateResponse(request, "login.html")


@router.get("/auth/google", include_in_schema=False)
async def start_google_sign_in(request: Request) -> Response:
    redirect_uri = request.url_for("auth_callback")
    if request.app.state.settings.secure_cookies:
        # Behind Vercel's proxy the app can see http; Google only accepts the registered https URI.
        redirect_uri = redirect_uri.replace(scheme="https")
    return await request.app.state.identity_provider.authorize_redirect(request, str(redirect_uri))


@router.get("/auth/callback", name="auth_callback", include_in_schema=False)
async def auth_callback(request: Request, db: DbSession) -> Response:
    try:
        token = await request.app.state.identity_provider.authorize_access_token(request)
    except OAuthError as error:
        logger.warning("Google sign-in did not complete: %s", error.error)
        return templates.TemplateResponse(request, "errors/sign_in_failed.html", status_code=400)

    userinfo = token.get("userinfo") or {}
    raw_email = str(userinfo.get("email") or "")
    verified = userinfo.get("email_verified") is True
    admins = request.app.state.settings.allowed_email_set
    # Passed raw, uncleaned: viewer_for's own ASCII check must see the address exactly as
    # Google sent it, or a look-alike character lowered here first could pass as a real one.
    viewer = await run_in_threadpool(_admit, db, admins, raw_email, userinfo) if verified else None
    if viewer is None:
        request.session.clear()  # a refused attempt must not leave an earlier session behind
        logger.warning("Refused sign-in for %s (email verified: %s)", raw_email or "<no email>", verified)
        return templates.TemplateResponse(request, "errors/access_denied.html", status_code=403)

    next_url = safe_next(request.session.get(SESSION_NEXT))
    request.session.clear()  # drop pre-login state so a planted session can't be carried in
    request.session[SESSION_USER] = {
        "email": viewer.email,
        "name": userinfo.get("name") or viewer.email,
        "picture": userinfo.get("picture"),
    }
    request.session[SESSION_CSRF] = secrets.token_urlsafe(32)
    logger.info("Signed in %s", viewer.email)
    return RedirectResponse(next_url, status_code=303)


def _admit(db: Session, admins: frozenset[str], email: str, userinfo: dict) -> Viewer | None:
    """The viewer for this email (admin or member), if any; records the sign-in when there is one."""
    viewer = viewer_for(db, email, admins)
    if viewer is None:
        return None
    record_sign_in(
        db,
        email=viewer.email,
        name=userinfo.get("name"),
        picture_url=userinfo.get("picture"),
        now=datetime.now(UTC),
    )
    db.commit()
    return viewer


@router.post("/logout", include_in_schema=False)
def logout(request: Request) -> Response:
    request.session.clear()
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)
