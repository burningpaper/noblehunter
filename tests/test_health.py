"""/health: always answers, and says whether the web app is configured, never how."""

from fastapi.testclient import TestClient

from web.app import app_from_environment, create_app
from web.settings import WebSettings

SETTINGS = WebSettings(
    web_database_url="postgresql+psycopg://noble_web:dbsecret@127.0.0.1:55432/noble_test",
    session_secret="s" * 48,
    google_client_id="123-abc.apps.googleusercontent.com",
    google_client_secret="GOCSPX-testsecret",
    allowed_emails="owner@example.com",
    secure_cookies=False,
)


def test_configured_app_reports_ok():
    response = TestClient(create_app(SETTINGS)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "configured": True}


def test_unconfigured_app_still_answers_health(monkeypatch, tmp_path):
    for name in (
        "WEB_DATABASE_URL",
        "SESSION_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "ALLOWED_EMAILS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    response = TestClient(app_from_environment()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "configured": False}


def test_health_never_leaks_secrets():
    body = TestClient(create_app(SETTINGS)).get("/health").text

    for secret in ("dbsecret", "GOCSPX-testsecret", "s" * 48):
        assert secret not in body
