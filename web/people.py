"""The People page, for admins: who is on which artist.

Every form swaps the whole page content back in, so the lists always match the database after
an add, rename or removal. Problems come back as 422 with what was typed kept. Adding someone
sends no email: Jarred tells them, and they sign in with that Google account.

A permanent live region (`#people-notice`) sits outside the swapped content and is refreshed
out of band on every POST, so a screen reader announces what happened even though the swap
itself replaces `#people-content` wholesale. `web/static/js/people.js` moves keyboard focus
there too, or to the first invalid field on a 422.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.models import Artist, User
from core.people import PeopleValidationError, add_member, list_artists, remove_member, rename_artist
from web.access import AdminViewer
from web.db import get_db
from web.forms import form_id
from web.templating import templates

router = APIRouter(prefix="/people")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]
NEW_ARTIST = "new"


@router.get("")
def people_page(request: Request, db: DbSession, viewer: AdminViewer) -> Response:
    return templates.TemplateResponse(request, "people/page.html", _context(request, db))


@router.post("/members")
def add(
    request: Request,
    db: DbSession,
    viewer: AdminViewer,
    email: FormText = "",
    artist_id: FormText = "",
    new_artist_name: FormText = "",
) -> Response:
    form = {"email": email, "artist_id": artist_id, "new_artist_name": new_artist_name}
    try:
        user = add_member(
            db,
            email=email,
            artist_id=_chosen_artist(artist_id),
            new_artist_name=new_artist_name,
            added_by=viewer.email,
        )
        db.commit()
    except PeopleValidationError as error:
        return _content(request, db, form=form, errors=error.errors, status_code=422)
    artist = _artist_named_for(db, artist_id, new_artist_name)
    return _content(
        request, db, notice=f"Added {user.email} to {artist}. They can sign in with that Google account."
    )


@router.post("/artists/{artist_id}/rename")
def rename(
    request: Request, artist_id: int, db: DbSession, viewer: AdminViewer, name: FormText = ""
) -> Response:
    try:
        artist = rename_artist(db, artist_id, name)
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    except PeopleValidationError as error:
        return _content(
            request,
            db,
            rename_errors={artist_id: error.errors["name"]},
            rename_values={artist_id: name},
            status_code=422,
        )
    return _content(request, db, notice=f"Renamed to {artist.name}.")


@router.post("/artists/{artist_id}/members/{user_id}/remove")
def remove(request: Request, artist_id: int, user_id: int, db: DbSession, viewer: AdminViewer) -> Response:
    artist, user = db.get(Artist, artist_id), db.get(User, user_id)
    if artist is None or user is None:
        raise HTTPException(status_code=404)
    try:
        remove_member(db, artist_id, user_id)
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    return _content(request, db, notice=f"Took {user.email} off {artist.name}.")


def _chosen_artist(raw: str) -> int | None:
    """None means "a new artist"; anything unreadable becomes 0, which matches no artist."""
    text = raw.strip()
    return None if text in ("", NEW_ARTIST) else form_id(text)


def _artist_named_for(db: Session, raw_artist_id: str, new_artist_name: str) -> str:
    chosen = _chosen_artist(raw_artist_id)
    artist = db.get(Artist, chosen) if chosen else None
    return artist.name if artist is not None else " ".join(new_artist_name.split())


def _context(request: Request, db: Session, **extra) -> dict:
    return {
        "artists": list_artists(db),
        "admin_emails": sorted(request.app.state.settings.allowed_email_set),
        "now": datetime.now(UTC),
        "form": {"email": "", "artist_id": "", "new_artist_name": ""},
        "errors": {},
        "rename_errors": {},
        "rename_values": {},
        "notice": None,
        "oob": False,
        **extra,
    }


def _content(request: Request, db: Session, *, status_code: int = 200, **extra) -> Response:
    return templates.TemplateResponse(
        request, "people/_response.html", _context(request, db, oob=True, **extra), status_code=status_code
    )
