"""Moving the test database to a given revision and back, for tests of data migrations.

These run outside the rolled-back `session` fixture, because DDL can't share its
transaction. Every test that uses them must put the database back at head in a `finally`.
"""

from alembic import command
from alembic.config import Config

from tests.conftest import REPO_ROOT, TEST_DATABASE_URL


def _config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    return config


def downgrade_to(revision: str) -> None:
    """Move the test database down to `revision`."""
    command.downgrade(_config(), revision)


def upgrade_to(revision: str = "head") -> None:
    """Move the test database up to `revision` (head by default)."""
    command.upgrade(_config(), revision)
