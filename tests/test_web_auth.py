"""Stage 2b: Sign in with Google, the session guard and CSRF.

A fake identity provider stands in for Google, so these tests exercise our rules (who gets
in, where they land, what's refused) without the network. Authlib itself verifies OAuth
state, the ID token signature and the nonce.
"""

import re

import pytest
from authlib.integrations.starlette_client import OAuthError
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from web.app import create_app
from web.settings import WebSettings

ALLOWED = "owner@example.com"


def settings(secure_cookies: bool = False) -> WebSettings:
    return WebSettings(
        web_database_url="postgresql+psycopg://noble_web:dbsecret@127.0.0.1:55432/noble_test",
        session_secret="s" * 48,
        google_client_id="123-abc.apps.googleusercontent.com",
        google_client_secret="GOCSPX-testsecret",
        allowed_emails=ALLOWED,
        secure_cookies=secure_cookies,
    )


class FakeGoogle:
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


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def client(google):
    return TestClient(create_app(settings(), identity_provider=google), follow_redirects=False)


def sign_in(client) -> None:
    assert client.get("/auth/google").status_code in (302, 303, 307)
    response = client.get("/auth/callback?state=fake&code=fake")
    assert response.status_code == 303, response.text


def csrf_token(client) -> str:
    html = client.get("/", headers={"accept": "text/html"}).text
    match = re.search(r'"X-CSRF-Token":\s*"([^"]+)"', html)
    assert match, "CSRF token not rendered for htmx"
    return match.group(1)


class TestGuard:
    def test_page_redirects_to_login_with_next(self, client):
        response = client.get("/", headers={"accept": "text/html"})

        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=%2F"

    def test_unknown_pages_also_require_login(self, client):
        response = client.get("/secret-admin", headers={"accept": "text/html"})

        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")

    def test_htmx_request_gets_401_and_client_redirect(self, client):
        response = client.get("/", headers={"hx-request": "true"})

        assert response.status_code == 401
        assert response.headers["hx-redirect"] == "/login"

    @pytest.mark.parametrize("path", ["/health", "/login", "/static/css/app.css"])
    def test_public_routes_need_no_login(self, client, path):
        assert client.get(path).status_code == 200

    def test_tampered_session_cookie_is_ignored(self, client):
        client.cookies.set("nh_session", "not-a-valid-signed-cookie")

        assert client.get("/", headers={"accept": "text/html"}).status_code == 303


class TestLogin:
    def test_login_page_offers_google_sign_in(self, client):
        html = client.get("/login").text

        assert "Sign in with Google" in html
        assert 'href="/auth/google"' in html

    def test_starting_sign_in_goes_to_google_with_our_callback(self, client, google):
        response = client.get("/auth/google")

        assert response.headers["location"].startswith("https://accounts.google.com/")
        assert google.redirect_uris[-1].endswith("/auth/callback")

    def test_allowed_verified_email_signs_in(self, client, google):
        google.userinfo["email"] = "Owner@Example.com"

        sign_in(client)

        page = client.get("/", headers={"accept": "text/html"})
        assert page.status_code == 200
        assert ALLOWED in page.text.lower()

    def test_already_signed_in_login_page_goes_home(self, client):
        sign_in(client)

        response = client.get("/login")

        assert response.status_code == 303
        assert response.headers["location"] == "/"

    def test_returns_to_the_page_that_needed_login(self, client):
        client.get("/login?next=%2Fprofiles%3Ftab%3Dterms")

        response = client.get("/auth/google")
        response = client.get("/auth/callback?state=fake&code=fake")

        assert response.headers["location"] == "/profiles?tab=terms"

    @pytest.mark.parametrize(
        "next_url", ["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:x"]
    )
    def test_external_next_is_ignored(self, client, next_url):
        client.get("/login", params={"next": next_url})
        client.get("/auth/google")

        response = client.get("/auth/callback?state=fake&code=fake")

        assert response.headers["location"] == "/"


class TestRefusals:
    def test_unverified_email_is_refused(self, client, google):
        google.userinfo["email_verified"] = False

        response = client.get("/auth/callback?state=fake&code=fake", headers={"accept": "text/html"})

        assert response.status_code == 403
        assert "not allowed" in response.text.lower()
        assert client.get("/", headers={"accept": "text/html"}).status_code == 303

    def test_unlisted_email_is_refused(self, client, google):
        google.userinfo["email"] = "stranger@example.com"

        response = client.get("/auth/callback?state=fake&code=fake", headers={"accept": "text/html"})

        assert response.status_code == 403
        assert client.get("/", headers={"accept": "text/html"}).status_code == 303

    def test_provider_error_is_refused_gracefully(self, client, google):
        google.error = OAuthError(error="mismatching_state", description="CSRF Warning! State not equal.")

        response = client.get("/auth/callback?state=wrong&code=fake", headers={"accept": "text/html"})

        assert response.status_code == 400
        assert "sign-in didn't complete" in response.text.lower()
        assert "Traceback" not in response.text


class TestSessionCookie:
    def test_cookie_is_httponly_lax_and_secure_in_production(self, google):
        secure = TestClient(
            create_app(settings(secure_cookies=True), identity_provider=google),
            base_url="https://testserver",
            follow_redirects=False,
        )
        secure.get("/auth/google")

        response = secure.get("/auth/callback?state=fake&code=fake")

        cookie = response.headers["set-cookie"].lower()
        assert "nh_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "secure" in cookie


class TestCsrf:
    def test_page_renders_csrf_header_for_htmx(self, client):
        sign_in(client)

        assert csrf_token(client)

    def test_unsafe_request_without_token_is_refused(self, client):
        sign_in(client)

        response = client.post("/logout")

        assert response.status_code == 403

    def test_unsafe_request_with_wrong_token_is_refused(self, client):
        sign_in(client)

        assert client.post("/logout", headers={"x-csrf-token": "wrong"}).status_code == 403

    def test_logout_with_token_ends_the_session(self, client):
        sign_in(client)
        token = csrf_token(client)

        response = client.post("/logout", headers={"x-csrf-token": token, "hx-request": "true"})

        assert response.status_code == 200
        assert response.headers["hx-redirect"] == "/login"
        assert client.get("/", headers={"accept": "text/html"}).status_code == 303


class TestSignedInPages:
    def test_unknown_page_gets_a_friendly_html_404_in_the_layout(self, client):
        sign_in(client)

        response = client.get("/definitely-not-a-page", headers={"accept": "text/html"})

        assert response.status_code == 404
        assert 'href="/static/css/app.css"' in response.text
        assert "not found" in response.text.lower()

    def test_signed_in_layout_offers_sign_out(self, client):
        sign_in(client)

        html = client.get("/", headers={"accept": "text/html"}).text

        assert 'hx-post="/logout"' in html
