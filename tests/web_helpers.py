"""Shared helpers for web tests: test settings, a fake Google, signing in, and a fake suggester."""

import re

from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from web.app import create_app
from web.db import get_db
from web.settings import WebSettings

ALLOWED = "owner@example.com"


def web_settings(secure_cookies: bool = False) -> WebSettings:
    # No env files and no Anthropic key: a test must never pick up the real key from .env.local.
    return WebSettings(
        _env_file=None,
        anthropic_api_key=None,
        web_database_url="postgresql+psycopg://noble_web:dbsecret@127.0.0.1:55432/noble_test",
        session_secret="s" * 48,
        google_client_id="123-abc.apps.googleusercontent.com",
        google_client_secret="GOCSPX-testsecret",
        allowed_emails=ALLOWED,
        secure_cookies=secure_cookies,
    )


class FakeGoogle:
    """Stands in for Google: redirects to a fake consent URL and returns configurable userinfo."""

    def __init__(self):
        self.userinfo = {"email": ALLOWED, "email_verified": True, "name": "Owner", "picture": None}
        self.error: Exception | None = None
        self.redirect_uris: list[str] = []

    async def authorize_redirect(self, request, redirect_uri):
        self.redirect_uris.append(str(redirect_uri))
        return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?state=fake", status_code=302)

    async def authorize_access_token(self, request):
        if self.error:
            raise self.error
        return {"userinfo": self.userinfo}


def sign_in(client: TestClient) -> None:
    assert client.get("/auth/google").status_code in (302, 303, 307)
    response = client.get("/auth/callback?state=fake&code=fake")
    assert response.status_code == 303, response.text


def csrf_token(client: TestClient) -> str:
    html = client.get("/", headers={"accept": "text/html"}).text
    match = re.search(r'"X-CSRF-Token":\s*"([^"]+)"', html)
    assert match, "CSRF token not rendered for htmx"
    return match.group(1)


def app_client(session, google: FakeGoogle | None = None, **app_options) -> TestClient:
    """A test client whose routes use the rolled-back test session. Not signed in."""
    app = create_app(web_settings(), identity_provider=google or FakeGoogle(), **app_options)

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    return TestClient(app, follow_redirects=False)


def member_client(session, email: str = "member@example.com", **app_options) -> TestClient:
    """A client signed in as `email`. Make them a member of an artist first, or sign-in is refused."""
    google = FakeGoogle()
    google.userinfo["email"] = email
    client = app_client(session, google, **app_options)
    sign_in(client)
    return client


class FakeSuggester:
    """Stands in for Ask Claude: returns canned suggestions, or raises, and records every call."""

    def __init__(self, suggestions=(), error: Exception | None = None):
        self.suggestions = list(suggestions)
        self.error = error
        self.calls: list[tuple] = []

    def suggest(self, section, prompt, context):
        self.calls.append((section, prompt, context))
        if self.error:
            raise self.error
        return list(self.suggestions)
