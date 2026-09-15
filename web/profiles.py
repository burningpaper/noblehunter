"""Profiles pages.

Deliberately thin: read the form, call `core.profiles`, render. Forms post with htmx and
swap their own fragment back in, so a validation error (HTTP 422) shows up right where
it happened without a page reload.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from core.models import Profile
from core.profile_rules import DEFAULT_DIGEST_TARGET
from core.profiles import (
    ProfileValidationError,
    activation_problems,
    artist_choices,
    create_profile,
    get_profile,
    list_profiles,
    set_profile_active,
    update_profile_settings,
)
from web.budget import budget_context
from web.db import get_db
from web.runs import panel_context
from web.templating import templates

router = APIRouter(prefix="/profiles")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]


@router.get("")
def profiles_page(request: Request, db: DbSession) -> Response:
    context = {
        "profiles": list_profiles(db),
        "form": {"name": "", "digest_target": DEFAULT_DIGEST_TARGET, "artist_id": ""},
        "errors": {},
        "artist_choices": artist_choices(db),
        **panel_context(db),
        **budget_context(db),
    }
    return templates.TemplateResponse(request, "profiles/list.html", context)


@router.post("")
def create(
    request: Request,
    db: DbSession,
    name: FormText = "",
    digest_target: FormText = "",
    artist_id: FormText = "",
) -> Response:
    try:
        profile = create_profile(db, _form_id(artist_id), name, digest_target)
        db.commit()
    except ProfileValidationError as error:
        context = {
            "form": {"name": name, "digest_target": digest_target, "artist_id": artist_id},
            "errors": error.errors,
            "artist_choices": artist_choices(db),
        }
        return templates.TemplateResponse(request, "profiles/_create_form.html", context, status_code=422)
    return _redirect(request, f"/profiles/{profile.id}")


def _form_id(raw: str) -> int:
    """A posted id, or 0 (which matches nothing) when it isn't a whole number."""
    text = raw.strip()
    return int(text) if text.isascii() and text.isdigit() else 0


@router.get("/{profile_id}")
def profile_page(request: Request, profile_id: int, db: DbSession) -> Response:
    profile = _profile_or_404(db, profile_id)
    context = {
        "profile": profile,
        "form": _settings_form(profile),
        "errors": {},
        "saved": False,
        "problems": activation_problems(profile),
    }
    return templates.TemplateResponse(request, "profiles/detail.html", context)


@router.post("/{profile_id}/settings")
def save_settings(
    request: Request,
    profile_id: int,
    db: DbSession,
    name: FormText = "",
    digest_target: FormText = "",
    min_followers: FormText = "",
) -> Response:
    profile = _profile_or_404(db, profile_id)
    try:
        # A blank floor (e.g. an older form) keeps the current value.
        update_profile_settings(db, profile_id, name, digest_target, min_followers.strip() or None)
        db.commit()
    except ProfileValidationError as error:
        typed = {"name": name, "digest_target": digest_target, "min_followers": min_followers}
        context = {"profile": profile, "form": typed, "errors": error.errors, "saved": False}
        return templates.TemplateResponse(request, "profiles/_settings_form.html", context, 422)
    context = {"profile": profile, "form": _settings_form(profile), "errors": {}, "saved": True}
    return templates.TemplateResponse(request, "profiles/_settings_form.html", context)


def _settings_form(profile: Profile) -> dict:
    return {
        "name": profile.name,
        "digest_target": profile.digest_target,
        "min_followers": profile.min_followers,
    }


@router.post("/{profile_id}/activate")
def activate(request: Request, profile_id: int, db: DbSession) -> Response:
    return _change_status(request, db, profile_id, active=True)


@router.post("/{profile_id}/pause")
def pause(request: Request, profile_id: int, db: DbSession) -> Response:
    return _change_status(request, db, profile_id, active=False)


def _change_status(request: Request, db: Session, profile_id: int, active: bool) -> Response:
    profile = _profile_or_404(db, profile_id)
    status_code, refused = 200, False
    try:
        set_profile_active(db, profile_id, active)
        db.commit()
    except ProfileValidationError:
        status_code, refused = 422, True
    context = {"profile": profile, "problems": activation_problems(profile), "refused": refused}
    return templates.TemplateResponse(request, "profiles/_status.html", context, status_code=status_code)


def _profile_or_404(db: Session, profile_id: int) -> Profile:
    try:
        return get_profile(db, profile_id)
    except LookupError:
        raise HTTPException(status_code=404) from None


def _redirect(request: Request, url: str) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)
