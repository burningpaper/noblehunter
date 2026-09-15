"""Run now and the nightly Claude budget spend everyone's money, so only admins get them."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from core.app_settings import nightly_claude_budget
from core.models import Run, RunRequest, RunStatus, RunTrigger
from core.run_status import MEMBER_WARNING
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
    response = a_member(session).get("/runs/status", headers={"hx-request": "true"})

    assert response.status_code == 200
    assert 'id="run-status"' in response.text
    assert 'hx-post="/runs/request"' not in response.text
    assert "Runs happen every night. An admin can start one sooner." in response.text


def test_a_member_cannot_request_a_run(session):
    response = post(a_member(session), "/runs/request")

    assert response.status_code == 403
    assert session.scalars(select(RunRequest)).all() == []


def test_a_member_cannot_change_the_budget(session):
    response = post(a_member(session), "/settings/claude-budget", {"budget": "9.00"})

    assert response.status_code == 403
    assert nightly_claude_budget(session) == Decimal("2.00")


def test_a_members_plain_post_to_the_budget_gets_the_admins_only_page(session):
    client = a_member(session)

    response = client.post(
        "/settings/claude-budget",
        data={"budget": "9.00"},
        headers={"x-csrf-token": csrf_token(client), "accept": "text/html"},
    )

    assert response.status_code == 403
    assert "Only admins" in response.text
    assert nightly_claude_budget(session) == Decimal("2.00")


def test_a_member_posting_an_invalid_budget_is_refused_not_explained(session):
    response = post(a_member(session), "/settings/claude-budget", {"budget": "-5"})

    assert response.status_code == 403
    assert nightly_claude_budget(session) == Decimal("2.00")


def test_an_admin_still_has_both(session):
    html = member_client(session, "owner@example.com").get("/profiles", headers=HTML).text

    assert 'hx-post="/runs/request"' in html
    assert 'hx-post="/settings/claude-budget"' in html


def test_a_failed_runs_raw_error_is_hidden_from_members_but_shown_to_admins(session):
    now = datetime.now(UTC)
    session.add(
        Run(
            trigger=RunTrigger.SCHEDULE,
            status=RunStatus.FAILED,
            error="SECRET-MARKER-123",
            started_at=now - timedelta(hours=1),
            heartbeat_at=now,
            finished_at=now,
        )
    )
    session.flush()
    member = a_member(session)

    profiles_html = member.get("/profiles", headers=HTML).text
    status_html = member.get("/runs/status", headers={"hx-request": "true"}).text
    admin_html = member_client(session, "owner@example.com").get("/profiles", headers=HTML).text

    assert "SECRET-MARKER-123" not in profiles_html
    assert MEMBER_WARNING in profiles_html
    assert "SECRET-MARKER-123" not in status_html
    assert MEMBER_WARNING in status_html
    assert "SECRET-MARKER-123" in admin_html
