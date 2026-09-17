"""The shared Jinja environment. Every page knows who's signed in and has the CSRF token for htmx."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from core.profile_contents import search_requests_per_night
from core.run_status import relative_time
from core.suggestions import PLACEHOLDERS, SECTIONS
from web.sessions import SESSION_CSRF, current_user

TEMPLATES_DIR = Path(__file__).parent / "templates"


def auth_context(request: Request) -> dict:
    has_session = "session" in request.scope
    user = current_user(request)
    # Set by web.access.current_viewer once access is decided; unset on pages rendered before
    # that (login, and errors for someone with no access at all).
    viewer = getattr(request.state, "viewer", None)
    return {
        "current_user": user,
        "csrf_token": request.session.get(SESSION_CSRF) if has_session else None,
        # Pages rendered without a per-request viewer get is_admin False: login, access-denied,
        # and a 404 for a URL that matches no route. Display only -- every admin action is still
        # enforced server-side by AdminViewer.
        "is_admin": viewer.is_admin if viewer is not None else False,
    }


def utc_clock(moment: datetime) -> str:
    """A UTC wall-clock reading, for the text inside a <time> element.

    That text is the fallback for a reader whose browser never runs the script that would put the
    time into their own zone, so it is labelled "UTC" in the template -- and converting here is
    what makes the label true. Postgres hands back a `timestamptz` in the connection's own zone:
    always the right instant, but not always wearing a UTC offset, and formatting it as-is would
    print a local clock reading under a UTC label. That is the bug being fixed, better dressed.
    """
    return moment.astimezone(UTC).strftime("%-d %b, %H:%M")


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[auth_context])
templates.env.globals["search_requests_per_night"] = search_requests_per_night
templates.env.globals["suggestion_labels"] = SECTIONS
templates.env.globals["suggestion_placeholders"] = PLACEHOLDERS
templates.env.globals["relative_time"] = relative_time
templates.env.globals["utc_clock"] = utc_clock
