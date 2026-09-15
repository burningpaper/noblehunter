"""Moving the test database to a given revision and back, for tests of data migrations.

These run outside the rolled-back `session` fixture, because DDL can't share its
transaction. Every test that uses them must put the database back at head in a `finally`.
"""

from alembic import command
from alembic.config import Config

from tests.conftest import REPO_ROOT, TEST_DATABASE_URL


def migrate_to(revision: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    if revision == "head":
        command.upgrade(config, "head")
    else:
        current = _current(config)
        if current is not None and current > revision:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)


def _current(config: Config) -> str | None:
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as connection:
            return connection.scalar(text("select version_num from alembic_version"))
    finally:
        engine.dispose()
