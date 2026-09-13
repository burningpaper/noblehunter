"""`pipeline.cli run` and `report`, wired through replaceable search and Spotify builders.

These commit real rows to the test database, so they wipe the pipeline tables before and after.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import pipeline.cli as cli
from core.profiles import create_profile, set_profile_active
from pipeline.search import QUERY_PREFIX, ProviderResult, SearchHit
from pipeline.spotify import PlaylistData, Track
from tests.conftest import TEST_DATABASE_URL
from tests.profile_helpers import add_contents

PLAYLIST_ID = "1" * 22


def wipe(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("truncate profiles, playlists, curators, runs restart identity cascade"))


class FakeProvider:
    name = "serper"

    def search(self, query: str) -> ProviderResult:
        if query.removeprefix(QUERY_PREFIX) != "term 0":
            return ProviderResult()
        return ProviderResult(hits=(SearchHit(PLAYLIST_ID, "serper", 1, "Night Machines | Spotify", ""),))


class FakeFetcher:
    def fetch_playlist(self, spotify_id: str) -> PlaylistData:
        now = datetime.now(UTC)
        tracks = tuple(
            Track(uid=str(i), name="t", artists=(f"Artist {i}",), added_at=now - timedelta(days=days))
            for i, days in enumerate((2, 20, 60))
        )
        return PlaylistData(
            spotify_id=spotify_id,
            name="Night Machines",
            description_html="",
            description_text="",
            owner_id="curator1",
            owner_name="Mimi",
            followers=900,
            total_tracks=40,
            cover_image_url=None,
            tracks=tracks,
            partial=False,
        )


@pytest.fixture
def pipeline_env(engine, monkeypatch):
    monkeypatch.setenv("PIPELINE_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-key")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
    monkeypatch.setattr(cli, "build_search_providers", lambda settings, client: [FakeProvider()])

    @contextmanager
    def fake_spotify():
        yield FakeFetcher()

    monkeypatch.setattr(cli, "open_spotify_client", fake_spotify)
    wipe(engine)
    with Session(engine) as session:
        profile = add_contents(session, create_profile(session, "Synman"))
        set_profile_active(session, profile.id, True)
        session.commit()
    yield engine
    wipe(engine)


def test_run_command_discovers_evaluates_and_summarises(pipeline_env):
    result = CliRunner().invoke(cli.app, ["run", "--fetch-limit", "5"])

    assert result.exit_code == 0, result.output
    assert "Synman" in result.output
    assert "term 0" in result.output
    assert "qualified 1" in result.output


def test_report_command_lists_what_the_run_found(pipeline_env):
    CliRunner().invoke(cli.app, ["run"])

    result = CliRunner().invoke(cli.app, ["report"])

    assert result.exit_code == 0, result.output
    assert "Night Machines" in result.output
    assert f"https://open.spotify.com/playlist/{PLAYLIST_ID}" in result.output


def test_run_without_search_keys_is_a_readable_error(pipeline_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # keep the repo's own env files out of it
    monkeypatch.delenv("SERPER_API_KEY")
    monkeypatch.delenv("BRAVE_API_KEY")

    result = CliRunner().invoke(cli.app, ["run"])

    assert result.exit_code == 1
    assert "SERPER_API_KEY" in result.output
    assert "Traceback" not in result.output
