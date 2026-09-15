"""The viewer behind each request, and how access problems are answered.

`current_viewer` rebuilds the viewer from the database on every request. `web/app.py` includes
every router except sign-in with it as a dependency, so even a route that never mentions the
viewer refuses someone who has been taken off all their artists. The answers:

- signed in, but no access any more: the session is cleared and the access-denied page shown
  (htmx requests are sent back to /login);
- something on another artist, or missing: 404 with the normal not-found page;
- an admin-only action by a member: 403.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from core.access import AdminOnly, NotVisible, Viewer, require_admin, viewer_for
from web.db import get_db
from web.sessions import current_user
from web.templating import templates


class NoAccess(Exception):
    """The signed-in email has no access (any more)."""


def current_viewer(request: Request, db: Annotated[Session, Depends(get_db)]) -> Viewer:
    user = current_user(request) or {}
    viewer = viewer_for(db, str(user.get("email") or ""), request.app.state.settings.allowed_email_set)
    if viewer is None:
        raise NoAccess()
    return viewer


CurrentViewer = Annotated[Viewer, Depends(current_viewer)]


def require_admin_viewer(viewer: CurrentViewer) -> Viewer:
    require_admin(viewer)
    return viewer


AdminViewer = Annotated[Viewer, Depends(require_admin_viewer)]


def install_access_handlers(app: FastAPI) -> None:
    @app.exception_handler(NoAccess)
    async def no_access(request: Request, error: NoAccess) -> Response:
        request.session.clear()
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return templates.TemplateResponse(request, "errors/access_denied.html", status_code=403)

    @app.exception_handler(NotVisible)
    async def not_visible(request: Request, error: NotVisible) -> Response:
        if "text/html" in request.headers.get("accept", ""):
            return templates.TemplateResponse(request, "errors/not_found.html", status_code=404)
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    @app.exception_handler(AdminOnly)
    async def admin_only(request: Request, error: AdminOnly) -> Response:
        return JSONResponse({"detail": str(error)}, status_code=403)
