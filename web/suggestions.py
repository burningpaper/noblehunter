"""Ask Claude routes: a question in, tick boxes out, ticked items added.

Asking returns only the suggestions fragment, swapped in under the question box, so the
section Jarred is looking at doesn't jump. Adding returns the whole section plus the status
panel, just like typing an entry in, with a short note in the panel saying what was added.
Anything Claude says is escaped like any other text, and a ticked item is re-validated on
the way in, so a tampered form can't add anything a typed entry couldn't.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core import suggestions
from core.access import require_profile
from core.models import Profile
from core.profiles import ProfileValidationError
from web.access import CurrentViewer
from web.db import get_db
from web.profile_contents import render_section
from web.templating import templates

router = APIRouter(prefix="/profiles/{profile_id}/suggest")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]
FormList = Annotated[list[str] | None, Form()]

NOT_SET_UP = "Ask Claude isn't set up yet: add ANTHROPIC_API_KEY to the web app's environment and redeploy."
UNREADABLE_CHOICE = "That suggestion couldn't be read. Ask again."


@router.post("/{section}")
def ask(
    request: Request,
    profile_id: int,
    section: str,
    db: DbSession,
    viewer: CurrentViewer,
    prompt: FormText = "",
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    section = _known_section(section)
    try:
        question = suggestions.validate_prompt(prompt)
    except ProfileValidationError as error:
        return _results(request, profile, section, notice=error.errors["prompt"], status_code=422)

    suggester = request.app.state.suggester
    if suggester is None:
        return _results(request, profile, section, notice=NOT_SET_UP)
    try:
        answer = suggester.suggest(section, question, suggestions.profile_context(profile))
    except suggestions.SuggestionError as error:
        return _results(request, profile, section, notice=str(error))
    return _results(request, profile, section, found=suggestions.new_suggestions(profile, section, answer))


@router.post("/{section}/add")
def add(
    request: Request,
    profile_id: int,
    section: str,
    db: DbSession,
    viewer: CurrentViewer,
    choice: FormList = None,
) -> Response:
    profile = require_profile(db, viewer, profile_id)
    section = _known_section(section)
    try:
        chosen = [_parse_choice(raw) for raw in choice or []]
        result = suggestions.add_suggestions(db, profile_id, section, chosen)
        db.commit()
    except ProfileValidationError as error:
        message = next(iter(error.errors.values()))
        return render_section(request, profile, section, notice=message, status_code=422)
    return render_section(request, profile, section, notice=result.summary)


def _results(
    request: Request,
    profile: Profile,
    section: str,
    *,
    found: list[suggestions.Suggestion] | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> Response:
    context = {
        "profile": profile,
        "section_key": section,
        "suggestions": found or [],
        "asked": found is not None,
        "notice": notice,
    }
    return templates.TemplateResponse(request, "profiles/_suggestions.html", context, status_code=status_code)


def _parse_choice(raw: str) -> suggestions.Suggestion:
    try:
        data = json.loads(raw)
        return suggestions.Suggestion(str(data["value"]), str(data.get("reason") or ""), data.get("kind"))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ProfileValidationError({"choice": UNREADABLE_CHOICE}) from None


def _known_section(section: str) -> str:
    if section not in suggestions.SECTIONS:
        raise HTTPException(status_code=404)
    return section
