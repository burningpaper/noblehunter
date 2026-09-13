"""Stage 2a: the web app's foundation. Settings, safety headers, layout, static files, DB session."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool

from core.settings import MissingSettingError
from web.app import app_from_environment, create_app
from web.db import build_engine
from web.settings import WebSettings, load_web_settings

WEB_ENV = {
    "WEB_DATABASE_URL": "postgresql://noble_web:dbsecret@ep-test-pooler.neon.tech/neondb?sslmode=require",
    "SESSION_SECRET": "s" * 48,
    "GOOGLE_CLIENT_ID": "123-abc.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "GOCSPX-testsecret",
    "ALLOWED_EMAILS": " Owner@Example.com , second@example.com ",
}


@pytest.fixture
def web_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # keep the repo's .env.local out of these tests
    for name in WEB_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def client(web_env):
    for name, value in WEB_ENV.items():
        web_env.setenv(name, value)
    return TestClient(create_app(load_web_settings(env_file=None)))


class TestWebSettings:
    def test_missing_settings_are_named(self, web_env):
        with pytest.raises(MissingSettingError) as error:
            load_web_settings(env_file=None)

        for name in ("WEB_DATABASE_URL", "SESSION_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
            assert name in str(error.value)

    def test_session_secret_must_be_long(self, web_env):
        for name, value in WEB_ENV.items():
            web_env.setenv(name, value)
        web_env.setenv("SESSION_SECRET", "too-short")

        with pytest.raises(MissingSettingError, match="SESSION_SECRET"):
            load_web_settings(env_file=None)

    def test_allowed_emails_are_trimmed_and_lowercased(self, web_env):
        for name, value in WEB_ENV.items():
            web_env.setenv(name, value)

        settings = load_web_settings(env_file=None)

        assert settings.allowed_email_set == {"owner@example.com", "second@example.com"}

    def test_cookies_are_secure_by_default(self, web_env):
        for name, value in WEB_ENV.items():
            web_env.setenv(name, value)

        assert load_web_settings(env_file=None).secure_cookies is True

    def test_errors_never_include_secret_values(self, web_env):
        for name, value in WEB_ENV.items():
            web_env.setenv(name, value)
        web_env.setenv("SESSION_SECRET", "short-but-secret")

        with pytest.raises(MissingSettingError) as error:
            load_web_settings(env_file=None)

        assert "short-but-secret" not in str(error.value)


class TestUnconfiguredApp:
    def test_pages_explain_the_app_is_not_configured(self, web_env):
        response = TestClient(app_from_environment()).get("/", headers={"accept": "text/html"})

        assert response.status_code == 503
        assert "not configured" in response.text.lower()


class TestSafetyHeaders:
    def test_every_response_carries_security_headers(self, client):
        headers = client.get("/health").headers

        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]


class TestLayoutAndStatic:
    def test_htmx_is_served_from_our_own_origin(self, client):
        response = client.get("/static/vendor/htmx.min.js")

        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_stylesheet_defines_design_tokens(self, client):
        css = client.get("/static/css/app.css").text

        for token in (
            "--color-bg",
            "--color-surface",
            "--color-text",
            "--color-accent",
            "--space-4",
            "--radius",
        ):
            assert token in css

    def test_public_login_page_uses_the_layout(self, client):
        response = client.get("/login", headers={"accept": "text/html"})

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'href="/static/css/app.css"' in response.text
        assert "<main" in response.text

    def test_layout_is_accessible_basics(self, client):
        html = client.get("/login", headers={"accept": "text/html"}).text

        assert '<html lang="en"' in html
        assert '<meta name="viewport"' in html
        assert 'class="skip-link"' in html


class TestDatabaseEngine:
    def test_engine_uses_web_role_url_without_client_pool(self, web_env):
        for name, value in WEB_ENV.items():
            web_env.setenv(name, value)

        engine = build_engine(load_web_settings(env_file=None))

        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.username == "noble_web"
        assert engine.url.host == "ep-test-pooler.neon.tech"
        assert isinstance(engine.pool, NullPool)


def test_settings_type_is_constructible_directly():
    settings = WebSettings(**{name.lower(): value for name, value in WEB_ENV.items()})

    assert settings.google_client_id.endswith(".apps.googleusercontent.com")
