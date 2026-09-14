"""The nightly Claude budget, edited on the Profiles page next to the runner status."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.app_settings import SettingsValidationError, nightly_claude_budget, set_nightly_claude_budget
from web.db import get_db
from web.templating import templates

router = APIRouter(prefix="/settings")
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]


@router.post("/claude-budget")
def save_budget(request: Request, db: DbSession, budget: FormText = "") -> Response:
    try:
        saved = set_nightly_claude_budget(db, budget)
        db.commit()
    except SettingsValidationError as error:
        context = {"budget_form": budget, "budget_error": error.errors["budget"], "budget_saved": False}
        return templates.TemplateResponse(request, "settings/_budget.html", context, status_code=422)
    context = {"budget_form": f"{saved:.2f}", "budget_error": None, "budget_saved": True}
    return templates.TemplateResponse(request, "settings/_budget.html", context)


def budget_context(db: Session) -> dict:
    return {"budget_form": f"{nightly_claude_budget(db):.2f}", "budget_error": None, "budget_saved": False}
