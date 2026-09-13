"""The shared Jinja environment. Every page knows who's signed in and has the CSRF token for htmx."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from core.profile_contents import search_requests_per_night
from web.sessions import SESSION_CSRF, current_user

TEMPLATES_DIR = Path(__file__).parent / "templates"


def auth_context(request: Request) -> dict:
    has_session = "session" in request.scope
    return {
        "current_user": current_user(request),
        "csrf_token": request.session.get(SESSION_CSRF) if has_session else None,
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[auth_context])
templates.env.globals["search_requests_per_night"] = search_requests_per_night
