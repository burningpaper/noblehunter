"""Putting recent rejections back in the queue, for when the rules that rejected them change.

Only reasons that are re-checked anyway (no fit, too small, not alive) can be requeued early.
Pay-to-play and Spotify's own playlists stay out for good.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import pipeline.cli as cli
from core.models import Playlist
from pipeline.recheck import requeue_recent_rejections
from tests.conftest import TEST_DATABASE_URL
from tests.factories import make_playlist
from tests.test_cli_pipeline import wipe

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def rejected(session, reason: str, days_ago: int, now: datetime = NOW) -> Playlist:
    return make_playlist(
        session, status="rejected", rejection_reason=reason, last_checked_at=now - timedelta(days=days_ago)
    )


def test_recent_no_fit_rejections_go_back_in_the_queue(session):
    recent = rejected(session, "no-fit", 2)
    old = rejected(session, "no-fit", 30)
    dead = rejected(session, "not-alive", 1)

    count = requeue_recent_rejections(session, reason="no-fit", since=NOW - timedelta(days=7))

    assert count == 1
    assert (recent.status, recent.rejection_reason) == ("candidate", None)
    assert old.status == "rejected"
    assert dead.status == "rejected"


@pytest.mark.parametrize("reason", ["not-real", "spotify-owned", "made-up"])
def test_permanent_or_unknown_reasons_are_never_requeued(session, reason):
    with pytest.raises(ValueError, match="can't be re-checked"):
        requeue_recent_rejections(session, reason=reason, since=NOW)


@pytest.fixture
def pipeline_database(engine, monkeypatch):
    monkeypatch.setenv("PIPELINE_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
    wipe(engine)
    yield engine
    wipe(engine)


def test_the_command_says_how_many_went_back(pipeline_database):
    with Session(pipeline_database) as session:
        rejected(session, "no-fit", 2, now=datetime.now(UTC))
        session.commit()

    result = CliRunner().invoke(cli.app, ["recheck", "--reason", "no-fit", "--days", "7"])

    assert result.exit_code == 0, result.output
    assert "1 playlist" in result.output
