"""Editing a profile's contents: one small route per action, one shared way of answering.

Every action first checks the profile is on one of the viewer's artists (404 if not), then
returns the section it changed plus the status panel as an htmx out-of-band swap, so the
readiness checklist and Active/Paused badge stay truthful without a reload. Validation
problems come back as a 422 section with the typed values kept.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core import profile_contents as contents
from core.access import Viewer, require_profile
from core.profiles import ProfileValidationError, activation_problems
from web.access import CurrentViewer
from web.db import get_db
from web.templating import templates

router = APIRouter(prefix="/profiles/{profile_id}")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]


@router.post("/genres")
def add_genre(
    request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer, tag: FormText = ""
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "genres",
        lambda: contents.add_genre(db, profile_id, tag),
        {"tag": tag},
    )


@router.post("/genres/{genre_id}/move")
def move_genre(
    request: Request,
    profile_id: int,
    genre_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    direction: FormText = "",
) -> Response:
    def move():
        if direction not in contents.MOVE_DIRECTIONS:
            raise HTTPException(status_code=400, detail="direction must be up or down")
        contents.move_genre(db, profile_id, genre_id, direction)

    return _apply(request, db, viewer, profile_id, "genres", move)


@router.delete("/genres/{genre_id}")
def remove_genre(
    request: Request, profile_id: int, genre_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    return _apply(
        request, db, viewer, profile_id, "genres", lambda: contents.remove_genre(db, profile_id, genre_id)
    )


@router.post("/artists")
def add_artist(
    request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer, name: FormText = ""
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "artists",
        lambda: contents.add_reference_artist(db, profile_id, name),
        {"name": name},
    )


@router.delete("/artists/{artist_id}")
def remove_artist(
    request: Request, profile_id: int, artist_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    """`artist_id` here is a reference artist on the profile, not an Artist who owns profiles."""
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "artists",
        lambda: contents.remove_reference_artist(db, profile_id, artist_id),
    )


@router.post("/anti-signals")
def add_anti_signal(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    kind: FormText = "",
    value: FormText = "",
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "anti_signals",
        lambda: contents.add_anti_signal(db, profile_id, kind, value),
        {"kind": kind, "value": value},
    )


@router.delete("/anti-signals/{signal_id}")
def remove_anti_signal(
    request: Request, profile_id: int, signal_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "anti_signals",
        lambda: contents.remove_anti_signal(db, profile_id, signal_id),
    )


@router.post("/tracks")
def add_track(
    request: Request,
    profile_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    title: FormText = "",
    spotify_url: FormText = "",
    description: FormText = "",
) -> Response:
    form = {"title": title, "spotify_url": spotify_url, "description": description}
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "tracks",
        lambda: contents.add_track(db, profile_id, title, spotify_url, description),
        form,
    )


@router.delete("/tracks/{track_id}")
def remove_track(
    request: Request, profile_id: int, track_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    return _apply(
        request, db, viewer, profile_id, "tracks", lambda: contents.remove_track(db, profile_id, track_id)
    )


@router.post("/terms")
def add_term(
    request: Request, profile_id: int, db: DbSession, viewer: CurrentViewer, term: FormText = ""
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "terms",
        lambda: contents.add_search_term(db, profile_id, term),
        {"term": term},
    )


@router.post("/terms/{term_id}/status")
def set_term_status(
    request: Request,
    profile_id: int,
    term_id: int,
    db: DbSession,
    viewer: CurrentViewer,
    status: FormText = "",
) -> Response:
    return _apply(
        request,
        db,
        viewer,
        profile_id,
        "terms",
        lambda: contents.set_search_term_status(db, profile_id, term_id, status),
    )


@router.delete("/terms/{term_id}")
def remove_term(
    request: Request, profile_id: int, term_id: int, db: DbSession, viewer: CurrentViewer
) -> Response:
    return _apply(
        request, db, viewer, profile_id, "terms", lambda: contents.remove_search_term(db, profile_id, term_id)
    )


def _apply(
    request: Request,
    db: Session,
    viewer: Viewer,
    profile_id: int,
    section: str,
    action: Callable[[], object],
    form: dict | None = None,
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    try:
        result = action()
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ProfileValidationError as error:
        return render_section(
            request, profile, section, errors=error.errors, form=form or {}, status_code=422
        )
    return render_section(request, profile, section, auto_paused=result is True)


def render_section(
    request: Request,
    profile,
    section: str,
    *,
    errors: dict | None = None,
    form: dict | None = None,
    auto_paused: bool = False,
    notice: str | None = None,
    status_code: int = 200,
) -> Response:
    """A section plus the status panel (out of band). `notice` is a short note for its Ask Claude panel."""
    context = {
        "profile": profile,
        "section": section,
        "section_errors": errors or {},
        "section_form": form or {},
        "section_notice": notice,
        "problems": activation_problems(profile),
        "auto_paused": auto_paused,
        "oob": True,
    }
    return templates.TemplateResponse(
        request, "profiles/_section_update.html", context, status_code=status_code
    )
