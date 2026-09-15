"""The shared Jinja environment. Every page knows who's signed in and has the CSRF token for htmx."""

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
        # Only decides what to *show*: every admin action is also enforced server-side by AdminViewer.
        "is_admin": viewer.is_admin if viewer is not None else False,
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[auth_context])
templates.env.globals["search_requests_per_night"] = search_requests_per_night
templates.env.globals["suggestion_labels"] = SECTIONS
templates.env.globals["suggestion_placeholders"] = PLACEHOLDERS
templates.env.globals["relative_time"] = relative_time
