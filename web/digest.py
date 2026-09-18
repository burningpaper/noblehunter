"""The digest page: the viewer's own night's curators to pitch, one click to record each verdict.

Every route here takes the signed-in viewer and only ever shows or changes entries on the
viewer's own artists; `require_outreach` refuses anything else as not found, before a verdict's
own validation runs. A verdict goes through `core.exclusion.record_verdict`, so its effects are
the same wherever it's recorded. `pitched` and `skip` let the curator come back after 90 days;
`bad-fit` and `dead` exclude the curator everywhere, for every artist, not just the one that
recorded the verdict, until stage 3 (Task 13/14) makes exclusion per artist. The entry swaps
itself back in with its new status, so working down the list never reloads the page.
"""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.access import Viewer, require_outreach, require_profile
from core.digest_view import digest_view, entry_view
from core.exclusion import record_verdict
from web.access import CurrentViewer
from web.db import get_db
from web.forms import form_id
from web.templating import templates

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
FormText = Annotated[str, Form()]

VERDICT_CHOICES = (("pitched", "Pitched"), ("skip", "Skip"), ("bad-fit", "Bad fit"), ("dead", "Dead"))
VERDICT_LABELS = dict(VERDICT_CHOICES)
UNKNOWN_VERDICT = "Choose pitched, skip, bad fit or dead."


@router.get("/digest")
def latest_digest(request: Request, db: DbSession, viewer: CurrentViewer, profile: str = "") -> Response:
    return _page(request, db, None, viewer, profile)


@router.get("/digest/{day}")
def digest_for_day(
    request: Request, day: str, db: DbSession, viewer: CurrentViewer, profile: str = ""
) -> Response:
    try:
        chosen = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=404) from None
    return _page(request, db, chosen, viewer, profile)


@router.post("/outreach/{outreach_id}/verdict")
def save_verdict(
    request: Request, outreach_id: int, db: DbSession, viewer: CurrentViewer, verdict: FormText = ""
) -> Response:
    require_outreach(db, viewer, outreach_id)
    now = datetime.now(UTC)
    try:
        record_verdict(db, outreach_id, verdict, now)
        db.commit()
    except LookupError:
        raise HTTPException(status_code=404) from None
    except ValueError:
        return _entry(request, db, outreach_id, now.date(), error=UNKNOWN_VERDICT, status_code=422)
    return _entry(request, db, outreach_id, now.date())


def _page(request: Request, db: Session, chosen: date | None, viewer: Viewer, profile: str) -> Response:
    chosen_profile = form_id(profile)
    if chosen_profile:
        require_profile(db, viewer, chosen_profile)  # another artist's id is not found, as everywhere else
    view = digest_view(
        db,
        chosen,
        today=datetime.now(UTC).date(),
        viewer=viewer,
        profile_id=chosen_profile or None,
    )
    return templates.TemplateResponse(request, "digest/page.html", {"view": view, **_verdict_context()})


def _entry(
    request: Request,
    db: Session,
    outreach_id: int,
    today: date,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    try:
        entry = entry_view(db, outreach_id, today=today)
    except LookupError:
        raise HTTPException(status_code=404) from None
    context = {"entry": entry, "verdict_error": error, **_verdict_context()}
    return templates.TemplateResponse(request, "digest/_entry.html", context, status_code=status_code)


def _verdict_context() -> dict:
    return {"verdict_choices": VERDICT_CHOICES, "verdict_labels": VERDICT_LABELS}
