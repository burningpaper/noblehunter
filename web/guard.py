"""The front door: default-deny sign-in and CSRF, applied to every request in one place.

New routes are protected automatically; only the handful of paths below are public. Every
unsafe request (POST, PUT, PATCH, DELETE) must carry the session's CSRF token in the
X-CSRF-Token header, which htmx sends from the page's hx-headers.
"""

import logging
import secrets
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from web.sessions import SESSION_CSRF, current_user

logger = logging.getLogger("noble_hunter.web.guard")

PUBLIC_PATHS = frozenset({"/health", "/login", "/auth/google", "/auth/callback"})
PUBLIC_PREFIXES = ("/static/",)
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def install_guard(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next) -> Response:
        if not _is_public(request.url.path) and not current_user(request):
            return _login_required(request)
        if request.method in UNSAFE_METHODS and not _csrf_ok(request):
            logger.warning("CSRF check failed on %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "Missing or invalid CSRF token."}, status_code=403)
        return await call_next(request)


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _login_required(request: Request) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def _csrf_ok(request: Request) -> bool:
    expected = request.session.get(SESSION_CSRF)
    provided = request.headers.get("x-csrf-token")
    return bool(expected and provided and secrets.compare_digest(expected, provided))
