"""`db create-roles`: make the web and pipeline logins and hand their URLs over safely.

Passwords are generated, never printed, and written only to a file readable by the
current user. Either both roles are created and granted, or nothing changes.
"""

import stat

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from pipeline.cli import app
from tests.conftest import TEST_DATABASE_URL

WEB = "cli_create_web"
PIPELINE = "cli_create_pipeline"


def role_exists(engine, role: str) -> bool:
    with engine.connect() as connection:
        return bool(connection.scalar(text("select 1 from pg_roles where rolname = :r"), {"r": role}))


def drop_roles(engine) -> None:
    with engine.begin() as connection:
        for role in (WEB, PIPELINE):
            if connection.scalar(text("select 1 from pg_roles where rolname = :r"), {"r": role}):
                connection.execute(text(f"DROP OWNED BY {role}"))
                connection.execute(text(f"DROP ROLE {role}"))


def read_env_file(path) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    return {key: value for key, value in pairs}


@pytest.fixture
def cli_env(engine, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", TEST_DATABASE_URL)
    drop_roles(engine)
    yield engine
    drop_roles(engine)


def invoke(output_path):
    args = [
        "db",
        "create-roles",
        "--web-role",
        WEB,
        "--pipeline-role",
        PIPELINE,
        "--output",
        str(output_path),
    ]
    return CliRunner().invoke(app, args)


def test_creates_both_roles_and_writes_working_urls(cli_env, tmp_path):
    output = tmp_path / ".env.roles.local"

    result = invoke(output)

    assert result.exit_code == 0, result.output
    urls = read_env_file(output)
    for variable, role in (("WEB_DATABASE_URL", WEB), ("PIPELINE_DATABASE_URL", PIPELINE)):
        login = create_engine(urls[variable])
        try:
            with login.connect() as connection:
                assert connection.scalar(text("select current_user")) == role
        finally:
            login.dispose()


def test_output_file_is_private_and_passwords_are_never_printed(cli_env, tmp_path):
    output = tmp_path / ".env.roles.local"

    result = invoke(output)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    for url in read_env_file(output).values():
        password = url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
        assert password not in result.output
    assert str(output) in result.output


def test_new_roles_get_least_privilege_grants(cli_env, tmp_path):
    invoke(tmp_path / ".env.roles.local")

    with cli_env.connect() as connection:
        assert connection.scalar(text(f"select has_table_privilege('{WEB}', 'profiles', 'INSERT')"))
        assert not connection.scalar(text(f"select has_table_privilege('{WEB}', 'playlists', 'INSERT')"))
        assert connection.scalar(text(f"select has_table_privilege('{PIPELINE}', 'playlists', 'INSERT')"))


def test_refuses_to_overwrite_an_existing_output_file(cli_env, tmp_path):
    output = tmp_path / ".env.roles.local"
    output.write_text("KEEP=me\n")

    result = invoke(output)

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert output.read_text() == "KEEP=me\n"
    assert not role_exists(cli_env, WEB)


def test_existing_role_means_nothing_is_created_or_written(cli_env, tmp_path):
    with cli_env.begin() as connection:
        connection.execute(text(f"CREATE ROLE {PIPELINE} NOLOGIN"))
    output = tmp_path / ".env.roles.local"

    result = invoke(output)

    assert result.exit_code == 1
    assert PIPELINE in result.output
    assert not role_exists(cli_env, WEB)
    assert not output.exists()
