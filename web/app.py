"""Noble Hunter web app, deployed to Vercel as `web.app:app`.

`create_app(settings)` builds the real app. `app_from_environment()` is what Vercel imports:
if settings are missing it still starts, answers /health, and shows a clear "not configured"
page, rather than failing at import with a stack trace nobody sees.

Middleware runs outermost first: security headers (so even redirects and 401s carry them),
then the signed session cookie, then the sign-in/CSRF guard, then the routes.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from core.settings import MissingSettingError
from core.suggestions import ClaudeSuggester, Suggester
from web.auth import IdentityProvider, google_provider
from web.auth import router as auth_router
from web.db import build_engine
from web.guard import install_guard
from web.profile_contents import router as profile_contents_router
from web.profiles import router as profiles_router
from web.sessions import SESSION_COOKIE, SESSION_MAX_AGE_SECONDS
from web.settings import WebSettings, load_web_settings
from web.suggestions import router as suggestions_router
from web.templating import TEMPLATES_DIR, templates

logger = logging.getLogger("noble_hunter.web")

STATIC_DIR = TEMPLATES_DIR.parent / "static"
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: https://i.scdn.co https://*.googleusercontent.com",
        "connect-src 'self'",
        "form-action 'self' https://accounts.google.com",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    ]
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
HSTS = "max-age=31536000; includeSubDomains"


def create_app(
    settings: WebSettings,
    identity_provider: IdentityProvider | None = None,
    suggester: Suggester | None = None,
) -> FastAPI:
    app = _base_app()
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.identity_provider = identity_provider or google_provider(settings)
    app.state.suggester = suggester or _claude_suggester(settings)
    app.include_router(auth_router)
    app.include_router(profiles_router)
    app.include_router(profile_contents_router)
    app.include_router(suggestions_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "configured": True}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return templates.TemplateResponse(request, "home.html")

    # Added innermost first: guard, then session, then security headers outermost.
    install_guard(app)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    _add_security_headers(app, hsts=settings.secure_cookies)
    return app


def _claude_suggester(settings: WebSettings) -> Suggester | None:
    """Ask Claude needs ANTHROPIC_API_KEY. Without it the app still runs and the panel says what's missing."""
    if settings.anthropic_api_key is None:
        return None
    return ClaudeSuggester.from_api_key(settings.anthropic_api_key.get_secret_value())


def create_unconfigured_app() -> FastAPI:
    app = _base_app()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "configured": False}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def not_configured(request: Request) -> Response:
        return templates.TemplateResponse(request, "errors/not_configured.html", status_code=503)

    _add_security_headers(app, hsts=False)
    return app


def app_from_environment() -> FastAPI:
    try:
        settings = load_web_settings()
    except MissingSettingError as error:
        logger.error("Web app is not configured: %s", error)
        return create_unconfigured_app()
    return create_app(settings)


def _base_app() -> FastAPI:
    app = FastAPI(title="Noble Hunter", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        if error.status_code == 404 and _wants_html(request):
            return templates.TemplateResponse(request, "errors/not_found.html", status_code=404)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(Exception)
    async def server_error(request: Request, error: Exception) -> Response:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        if _wants_html(request):
            return templates.TemplateResponse(request, "errors/server_error.html", status_code=500)
        return JSONResponse({"detail": "Something went wrong."}, status_code=500)

    return app


def _add_security_headers(app: FastAPI, hsts: bool) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if hsts:
            response.headers["Strict-Transport-Security"] = HSTS
        return response


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


app = app_from_environment()
