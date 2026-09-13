"""The `db grant` command, run against the test database with real (temporary) roles."""

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from pipeline.cli import app
from tests.conftest import TEST_DATABASE_URL

WEB = "cli_test_web"
PIPELINE = "cli_test_pipeline"


@pytest.fixture
def temporary_roles(engine, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", TEST_DATABASE_URL)
    with engine.begin() as connection:
        for role in (WEB, PIPELINE):
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
            connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
    yield engine
    with engine.begin() as connection:
        for role in (WEB, PIPELINE):
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE {role}"))


def test_grant_applies_privileges(temporary_roles):
    result = CliRunner().invoke(app, ["db", "grant", "--web-role", WEB, "--pipeline-role", PIPELINE])

    assert result.exit_code == 0, result.output
    assert WEB in result.output and PIPELINE in result.output
    with temporary_roles.connect() as connection:
        assert connection.scalar(text(f"select has_table_privilege('{WEB}', 'profiles', 'INSERT')"))
        assert not connection.scalar(text(f"select has_table_privilege('{WEB}', 'playlists', 'INSERT')"))
        assert connection.scalar(text(f"select has_table_privilege('{PIPELINE}', 'playlists', 'INSERT')"))


def test_grant_with_missing_role_exits_with_readable_error(engine, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", TEST_DATABASE_URL)

    result = CliRunner().invoke(
        app, ["db", "grant", "--web-role", "no_such_web", "--pipeline-role", PIPELINE]
    )

    assert result.exit_code == 1
    assert "no_such_web" in result.output
    assert "Traceback" not in result.output
