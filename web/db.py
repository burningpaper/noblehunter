"""Database access for the web app.

Vercel runs many short-lived instances and Neon's PgBouncer already pools connections, so
the engine keeps no client-side pool. psycopg's automatic prepared statements are switched
off because they don't survive PgBouncer handing each transaction a different server
connection.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from web.settings import WebSettings

CONNECT_TIMEOUT_SECONDS = 10


def build_engine(settings: WebSettings) -> Engine:
    return create_engine(
        settings.sqlalchemy_url(),
        poolclass=NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS, "prepare_threshold": None},
    )


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards."""
    with Session(request.app.state.engine) as session:
        yield session
