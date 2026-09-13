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

from sqlalchemy import Connection, text

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
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
