"""Run now, and the runner status panel.

The web app never runs the pipeline: a run takes far longer than a Vercel function may. "Run
now" leaves a request that the runner on the Mac Mini picks up within a minute, and the panel
re-reads the status every 30 seconds, so Jarred can watch a run go from queued to running to
finished without reloading.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.run_status import request_run, run_status
from web.db import get_db
from web.sessions import current_user
from web.templating import templates

router = APIRouter(prefix="/runs")
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/status")
def status_panel(request: Request, db: DbSession) -> Response:
    return _render_panel(request, db)


@router.post("/request")
def run_now(request: Request, db: DbSession) -> Response:
    user = current_user(request) or {}
    request_run(db, requested_by=str(user.get("email") or "unknown"))
    db.commit()
    return _render_panel(request, db)


def panel_context(db: Session) -> dict:
    now = datetime.now(UTC)
    return {"run_status_view": run_status(db, now), "now": now}


def _render_panel(request: Request, db: Session) -> Response:
    context = {**panel_context(db), "refreshed": True}
    return templates.TemplateResponse(request, "runs/_panel.html", context)
