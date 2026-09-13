"""Least-privilege grants for the web app and pipeline database roles.

The roles themselves are created in the Neon console (so their passwords never pass
through code or chat). This module then gives each exactly what it needs, run as the
database owner:

- web (Vercel, public internet): read everything; manage profile settings, search terms
  and "Run now" requests; record verdicts on outreach and exclusions on curators, column
  by column. It can't rewrite pipeline results, delete digest history or change schema.
- pipeline (Mac Mini): read and write all data tables; read-only on migration history;
  no schema changes.

Every call starts by revoking everything, so it's safe to re-run after each migration.
"""

import re
import secrets

from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PASSWORD_BYTES = 32  # 256 bits of entropy; Neon requires at least 60
MIGRATION_TABLE = "alembic_version"

WEB_EDITABLE_TABLES = (
    "profiles",
    "profile_genres",
    "reference_artists",
    "anti_signals",
    "profile_tracks",
    "search_terms",
    "run_requests",
)
WEB_OUTREACH_COLUMNS = ("status", "status_changed_at", "pitched_at", "notes")
WEB_CURATOR_COLUMNS = ("excluded_at", "exclusion_reason")


def generate_password() -> str:
    return secrets.token_urlsafe(PASSWORD_BYTES)


def create_login_role(connection: Connection, role: str) -> str:
    """Create a LOGIN role with no special attributes or memberships and return its password.

    Must be done with SQL, never in the Neon console: console-created roles join
    `neon_superuser`. The password is hashed to SCRAM-SHA-256 on this machine, so only
    the hash is sent to the server (and could ever appear in a statement log).
    """
    _check_role_name(role)
    if connection.scalar(text("select 1 from pg_roles where rolname = :role"), {"role": role}):
        raise ValueError(f"Role {role!r} already exists; refusing to overwrite its password")

    password = generate_password()
    verifier = _scram_verifier(connection, role, password)
    # exec_driver_sql, not text(): a SCRAM verifier can contain "=:" which text() would
    # misread as a bind parameter. Role name is validated; the verifier is base64 and '$'.
    connection.exec_driver_sql(
        f"CREATE ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
        f"PASSWORD '{verifier}'"
    )
    return password


def connection_url_for(base_url: str, role: str, password: str) -> str:
    """The base URL (host, database, SSL options) with the role's credentials swapped in."""
    return make_url(base_url).set(username=role, password=password).render_as_string(hide_password=False)


def _scram_verifier(connection: Connection, role: str, password: str) -> str:
    pgconn = connection.connection.driver_connection.pgconn
    verifier = pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
    if "'" in verifier:
        raise RuntimeError("Unexpected quote in SCRAM verifier")
    return verifier


def grant_privileges(connection: Connection, web_role: str, pipeline_role: str) -> None:
    for role in (web_role, pipeline_role):
        _check_role_name(role)
    for role in (web_role, pipeline_role):
        _require_role(connection, role)

    for statement in _statements(web_role, pipeline_role, _data_tables(connection)):
        connection.execute(text(statement))


def _statements(web: str, pipeline: str, data_tables: list[str]) -> list[str]:
    both = f"{web}, {pipeline}"
    return [
        # Start from nothing, so re-running never leaves stale privileges behind.
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {both}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {both}",
        f"REVOKE CREATE ON SCHEMA public FROM {both}",
        f"GRANT USAGE ON SCHEMA public TO {both}",
        # Web app.
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {web}",
        f"GRANT INSERT, UPDATE, DELETE ON {_table_list(WEB_EDITABLE_TABLES)} TO {web}",
        f"GRANT UPDATE ({', '.join(WEB_OUTREACH_COLUMNS)}) ON outreach TO {web}",
        f"GRANT UPDATE ({', '.join(WEB_CURATOR_COLUMNS)}) ON curators TO {web}",
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {web}",
        # Pipeline.
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_table_list(data_tables)} TO {pipeline}",
        f'GRANT SELECT ON "{MIGRATION_TABLE}" TO {pipeline}',
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {pipeline}",
    ]


def _check_role_name(role: str) -> None:
    # Identifiers can't be bound as parameters, so only plain lowercase names are allowed.
    if not ROLE_NAME.match(role):
        raise ValueError(f"Invalid role name {role!r}: use lowercase letters, digits and underscores")


def _require_role(connection: Connection, role: str) -> None:
    exists = connection.scalar(text("select 1 from pg_roles where rolname = :role"), {"role": role})
    if not exists:
        raise LookupError(f"Role {role!r} does not exist. Create it in the Neon console first.")


def _data_tables(connection: Connection) -> list[str]:
    rows = connection.scalars(
        text(
            "select tablename from pg_tables where schemaname = 'public' and tablename <> :migration "
            "order by tablename"
        ),
        {"migration": MIGRATION_TABLE},
    )
    return list(rows)


def _table_list(tables) -> str:
    return ", ".join(f'"{table}"' for table in tables)
