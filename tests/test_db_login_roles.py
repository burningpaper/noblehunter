"""Creating the web and pipeline login roles with SQL.

Neon adds console-created roles to `neon_superuser` (pg_write_all_data, CREATEROLE,
BYPASSRLS...), which would make any GRANT meaningless. Roles created with SQL start with
nothing, so that's how these are made. These tests commit real roles (a login has to be
visible to a second connection) and remove them afterwards.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from core.db_roles import connection_url_for, create_login_role, generate_password
from tests.conftest import TEST_DATABASE_URL

ROLE = "login_test_role"


def drop_role(engine, role: str) -> None:
    with engine.begin() as connection:
        if connection.scalar(text("select 1 from pg_roles where rolname = :r"), {"r": role}):
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE {role}"))


@pytest.fixture
def fresh_role(engine):
    drop_role(engine, ROLE)
    yield ROLE
    drop_role(engine, ROLE)


def test_created_role_can_log_in(engine, fresh_role):
    with engine.begin() as connection:
        password = create_login_role(connection, fresh_role)

    login = create_engine(connection_url_for(TEST_DATABASE_URL, fresh_role, password))
    try:
        with login.connect() as connection:
            assert connection.scalar(text("select current_user")) == fresh_role
    finally:
        login.dispose()


def test_created_role_has_no_special_powers_or_memberships(engine, fresh_role):
    with engine.begin() as connection:
        create_login_role(connection, fresh_role)

    with engine.connect() as connection:
        role = connection.execute(
            text(
                "select rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin "
                "from pg_roles where rolname = :r"
            ),
            {"r": fresh_role},
        ).one()
        memberships = connection.scalar(
            text(
                "select count(*) from pg_auth_members m "
                "join pg_roles r on r.oid = m.member where r.rolname = :r"
            ),
            {"r": fresh_role},
        )

    assert tuple(role) == (False, False, False, False, False, True)
    assert memberships == 0


def test_password_is_stored_as_a_scram_hash(engine, fresh_role):
    with engine.begin() as connection:
        password = create_login_role(connection, fresh_role)
        stored = connection.scalar(
            text("select rolpassword from pg_authid where rolname = :r"), {"r": fresh_role}
        )

    assert stored.startswith("SCRAM-SHA-256$")
    assert password not in stored


def test_existing_role_is_never_overwritten(engine, fresh_role):
    with engine.begin() as connection:
        create_login_role(connection, fresh_role)

    with pytest.raises(ValueError, match="already exists"), engine.begin() as connection:
        create_login_role(connection, fresh_role)


def test_unsafe_role_name_is_rejected(engine):
    with pytest.raises(ValueError, match="role name"), engine.begin() as connection:
        create_login_role(connection, "bad name; drop table profiles")


def test_generated_passwords_are_long_and_unique():
    first, second = generate_password(), generate_password()

    assert len(first) >= 40
    assert first != second


def test_connection_url_swaps_credentials_and_keeps_everything_else():
    base = "postgresql+psycopg://owner:ownersecret@ep-test-pooler.neon.tech:5432/neondb?sslmode=require"

    url = make_url(connection_url_for(base, "noble_web", "p@ss/word:with#chars"))

    assert url.username == "noble_web"
    assert url.password == "p@ss/word:with#chars"
    assert url.host == "ep-test-pooler.neon.tech"
    assert url.database == "neondb"
    assert url.query["sslmode"] == "require"
    assert "ownersecret" not in connection_url_for(base, "noble_web", "x" * 40)
