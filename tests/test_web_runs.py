"""Run now and the runner status panel on the Profiles page."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.models import RunRequest, RunRequestStatus, WorkerState, WorkerStatus
from tests.web_helpers import ALLOWED, FakeGoogle, csrf_token, sign_in, web_settings
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


def htmx_post(client: TestClient, path: str, data: dict | None = None):
    headers = {"x-csrf-token": csrf_token(client), "hx-request": "true"}
    return client.post(path, data=data or {}, headers=headers)


class TestStatusPanel:
    def test_the_profiles_page_shows_the_panel_with_run_now(self, client):
        html = client.get("/profiles", headers={"accept": "text/html"}).text

        assert 'id="run-status"' in html
        assert 'hx-post="/runs/request"' in html
        assert "never checked in" in html

    def test_the_panel_refreshes_itself(self, client):
        response = client.get("/runs/status", headers={"hx-request": "true"})

        assert response.status_code == 200
        assert 'id="run-status"' in response.text
        assert 'hx-trigger="every 30s"' in response.text

    def test_an_online_runner_is_shown_as_online(self, client, session):
        now = datetime.now(UTC)
        session.add(
            WorkerStatus(id=1, hostname="mac-mini", state=WorkerState.IDLE, started_at=now, heartbeat_at=now)
        )
        session.flush()

        assert "Runner online" in client.get("/runs/status").text


class TestRunNow:
    def test_clicking_run_now_twice_queues_one_request(self, client, session):
        first = htmx_post(client, "/runs/request")
        htmx_post(client, "/runs/request")

        assert first.status_code == 200
        assert "Queued" in first.text
        requests = session.scalars(select(RunRequest)).all()
        assert len(requests) == 1
        assert requests[0].requested_by == ALLOWED
        assert requests[0].status == RunRequestStatus.PENDING

    def test_run_now_needs_the_csrf_token(self, client):
        response = client.post("/runs/request", headers={"hx-request": "true"})

        assert response.status_code == 403
