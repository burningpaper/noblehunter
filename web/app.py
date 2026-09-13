"""Noble Hunter web app, deployed to Vercel as `web.app:app`.

`create_app(settings)` builds the real app. `app_from_environment()` is what Vercel imports:
if settings are missing it still starts, answers /health, and shows a clear "not configured"
page, rather than failing at import with a stack trace nobody sees.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.settings import MissingSettingError
from web.db import build_engine
from web.settings import WebSettings, load_web_settings

logger = logging.getLogger("noble_hunter.web")

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

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


def create_app(settings: WebSettings) -> FastAPI:
    app = _base_app(hsts=settings.secure_cookies)
    app.state.settings = settings
    app.state.engine = build_engine(settings)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "configured": True}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return templates.TemplateResponse(request, "home.html")

    return app


def create_unconfigured_app() -> FastAPI:
    app = _base_app(hsts=False)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "configured": False}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def not_configured(request: Request) -> Response:
        return templates.TemplateResponse(request, "errors/not_configured.html", status_code=503)

    return app


def app_from_environment() -> FastAPI:
    try:
        settings = load_web_settings()
    except MissingSettingError as error:
        logger.error("Web app is not configured: %s", error)
        return create_unconfigured_app()
    return create_app(settings)


def _base_app(hsts: bool) -> FastAPI:
    app = FastAPI(title="Noble Hunter", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if hsts:
            response.headers["Strict-Transport-Security"] = HSTS
        return response

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


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


app = app_from_environment()
