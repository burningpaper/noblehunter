"""Alembic environment.

Migrations always run over a direct (unpooled) connection: PgBouncer's transaction pooling
doesn't mix well with DDL. Tests pass their own URL through config.attributes.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from core.models import Base
from core.settings import load_settings

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return config.attributes.get("database_url") or load_settings().sqlalchemy_url(pooled=False)


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
