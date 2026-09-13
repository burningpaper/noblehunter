from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app)


def test_health_reports_ok_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database_configured": False}


def test_health_detects_database_without_leaking_it(monkeypatch):
    secret_url = "postgresql://user:secret@example.neon.tech/db"
    monkeypatch.setenv("DATABASE_URL", secret_url)

    response = client.get("/health")

    assert response.json()["database_configured"] is True
    assert "secret" not in response.text


def test_home_page_renders():
    response = client.get("/")

    assert response.status_code == 200
    assert "Noble Hunter" in response.text


def test_api_docs_are_not_exposed():
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
