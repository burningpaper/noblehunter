"""Run now and the nightly Claude budget spend everyone's money, so only admins get them."""

from decimal import Decimal

from sqlalchemy import select

from core.app_settings import nightly_claude_budget
from core.models import RunRequest
from tests.factories import make_artist, make_member, make_user
from tests.web_helpers import csrf_token, member_client

HTML = {"accept": "text/html"}


def a_member(session):
    make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    return member_client(session, "nik@example.com")


def post(client, path: str, data: dict | None = None):
    return client.post(
        path, data=data or {}, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"}
    )


def test_members_see_the_runner_panel_without_run_now_or_the_budget(session):
    html = a_member(session).get("/profiles", headers=HTML).text

    assert 'id="run-status"' in html
    assert 'hx-post="/runs/request"' not in html
    assert 'hx-post="/settings/claude-budget"' not in html


def test_the_refreshed_panel_has_no_run_now_for_members(session):
    html = a_member(session).get("/runs/status", headers={"hx-request": "true"}).text

    assert 'hx-post="/runs/request"' not in html


def test_a_member_cannot_request_a_run(session):
    response = post(a_member(session), "/runs/request")

    assert response.status_code == 403
    assert session.scalars(select(RunRequest)).all() == []


def test_a_member_cannot_change_the_budget(session):
    response = post(a_member(session), "/settings/claude-budget", {"budget": "9.00"})

    assert response.status_code == 403
    assert nightly_claude_budget(session) == Decimal("2.00")


def test_an_admin_still_has_both(session):
    html = member_client(session, "owner@example.com").get("/profiles", headers=HTML).text

    assert 'hx-post="/runs/request"' in html
    assert 'hx-post="/settings/claude-budget"' in html
