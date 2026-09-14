"""The nightly Claude budget, set on the Profiles page next to the runner status."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from core.app_settings import nightly_claude_budget
from tests.web_helpers import FakeGoogle, csrf_token, sign_in, web_settings
from web.app import create_app
from web.db import get_db


@pytest.fixture
def client(session):
    app = create_app(web_settings(), identity_provider=FakeGoogle())

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    test_client = TestClient(app, follow_redirects=False)
    sign_in(test_client)
    return test_client


def htmx_post(client: TestClient, path: str, data: dict):
    return client.post(path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


def test_the_profiles_page_shows_the_budget(client):
    html = client.get("/profiles", headers={"accept": "text/html"}).text

    assert 'hx-post="/settings/claude-budget"' in html
    assert 'name="budget"' in html
    assert 'value="2.00"' in html


def test_saving_a_new_budget(client, session):
    response = htmx_post(client, "/settings/claude-budget", {"budget": "3.50"})

    assert response.status_code == 200
    assert "Saved" in response.text
    assert 'value="3.50"' in response.text
    assert nightly_claude_budget(session) == Decimal("3.50")


def test_an_unreasonable_budget_is_explained_in_place(client, session):
    response = htmx_post(client, "/settings/claude-budget", {"budget": "-5"})

    assert response.status_code == 422
    assert "between" in response.text
    assert 'value="-5"' in response.text
    assert nightly_claude_budget(session) == Decimal("2.00")
