"""Shared fixtures.

Database tests run against a real Postgres (the constraints ARE the product: a duplicate
digest entry must be impossible, not just unlikely). Start it with `scripts/test-db.sh up`,
or point TEST_DATABASE_URL at another disposable database. Never point it at production:
the fixture drops and recreates the public schema.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).parent.parent
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/noble_test"
)


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError:
        pytest.fail(
            "Test Postgres is not reachable. Start it with `scripts/test-db.sh up` "
            "(needs Docker) or set TEST_DATABASE_URL to a disposable database.",
            pytrace=False,
        )

    with engine.begin() as connection:
        connection.execute(text("drop schema if exists public cascade"))
        connection.execute(text("create schema public"))
    _migrate_to_head(TEST_DATABASE_URL)

    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """A session whose work is rolled back after each test, even if the test commits."""
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _migrate_to_head(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked `live`, which hit real external services such as Spotify.",
    )


def pytest_collection_modifyitems(config, items):
    """Live tests are slow and talk to the outside world, so they only run when asked for."""
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live test: run with --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
