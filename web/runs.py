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

from core.access import Viewer
from core.run_status import member_warnings, request_run, run_status
from web.access import AdminViewer, CurrentViewer
from web.db import get_db
from web.templating import templates

router = APIRouter(prefix="/runs")
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/status")
def status_panel(request: Request, db: DbSession, viewer: CurrentViewer) -> Response:
    return _render_panel(request, db, viewer)


@router.post("/request")
def run_now(request: Request, db: DbSession, viewer: AdminViewer) -> Response:
    request_run(db, requested_by=viewer.email)
    db.commit()
    return _render_panel(request, db, viewer)


def panel_context(db: Session, viewer: Viewer) -> dict:
    now = datetime.now(UTC)
    status = run_status(db, now)
    warnings = status.warnings if viewer.is_admin else member_warnings(status.warnings)
    return {
        "run_status_view": status,
        "now": now,
        "can_run_now": viewer.is_admin,
        "run_warnings": warnings,
    }


def _render_panel(request: Request, db: Session, viewer: Viewer) -> Response:
    context = {**panel_context(db, viewer), "refreshed": True}
    return templates.TemplateResponse(request, "runs/_panel.html", context)
