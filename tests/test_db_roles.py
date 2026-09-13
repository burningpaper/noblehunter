"""Least-privilege database roles for the two halves of the system.

The web app is on the public internet, so its role can edit settings and verdicts but
can't rewrite pipeline results, delete digest history, or change the schema. The
pipeline can read and write data but never change the schema or migration history.
Roles are created in a rolled-back transaction, so tests leave nothing behind.
"""

import pytest
from sqlalchemy import text

from core.db_roles import grant_privileges

WEB = "test_noble_web"
PIPELINE = "test_noble_pipeline"


@pytest.fixture
def roles(session):
    for role in (WEB, PIPELINE):
        session.execute(text(f"CREATE ROLE {role} NOLOGIN"))
    grant_privileges(session.connection(), web_role=WEB, pipeline_role=PIPELINE)
    return session


def can(session, role: str, table: str, privilege: str) -> bool:
    return session.scalar(
        text("select has_table_privilege(:role, :table, :privilege)"),
        {"role": role, "table": table, "privilege": privilege},
    )


def can_on_column(session, role: str, table: str, column: str, privilege: str) -> bool:
    return session.scalar(
        text("select has_column_privilege(:role, :table, :column, :privilege)"),
        {"role": role, "table": table, "column": column, "privilege": privilege},
    )


class TestWebRole:
    @pytest.mark.parametrize("table", ["profiles", "playlists", "outreach", "runs", "contacts"])
    def test_can_read_everything(self, roles, table):
        assert can(roles, WEB, table, "SELECT")

    @pytest.mark.parametrize(
        "table",
        ["profiles", "profile_genres", "reference_artists", "anti_signals", "profile_tracks", "search_terms"],
    )
    def test_can_manage_profile_settings(self, roles, table):
        assert all(can(roles, WEB, table, privilege) for privilege in ("INSERT", "UPDATE", "DELETE"))

    def test_can_request_a_run(self, roles):
        assert can(roles, WEB, "run_requests", "INSERT")

    @pytest.mark.parametrize("column", ["status", "status_changed_at", "pitched_at", "notes"])
    def test_can_record_verdicts(self, roles, column):
        assert can_on_column(roles, WEB, "outreach", column, "UPDATE")

    @pytest.mark.parametrize("column", ["brief_text", "digest_date", "curator_id"])
    def test_cannot_rewrite_digest_entries(self, roles, column):
        assert not can_on_column(roles, WEB, "outreach", column, "UPDATE")

    def test_can_exclude_a_curator(self, roles):
        assert can_on_column(roles, WEB, "curators", "excluded_at", "UPDATE")
        assert can_on_column(roles, WEB, "curators", "exclusion_reason", "UPDATE")

    @pytest.mark.parametrize(
        "table, privilege",
        [
            ("outreach", "DELETE"),
            ("playlists", "INSERT"),
            ("contacts", "UPDATE"),
            ("runs", "INSERT"),
            ("alembic_version", "UPDATE"),
        ],
    )
    def test_cannot_touch_pipeline_data_or_history(self, roles, table, privilege):
        assert not can(roles, WEB, table, privilege)

    def test_cannot_change_schema(self, roles):
        assert not roles.scalar(text("select has_schema_privilege(:role, 'public', 'CREATE')"), {"role": WEB})


class TestPipelineRole:
    @pytest.mark.parametrize(
        "table", ["playlists", "contacts", "outreach", "runs", "curators", "playlist_sources"]
    )
    def test_can_read_and_write_data(self, roles, table):
        assert all(can(roles, PIPELINE, table, p) for p in ("SELECT", "INSERT", "UPDATE", "DELETE"))

    def test_can_use_id_sequences(self, roles):
        assert roles.scalar(
            text("select has_sequence_privilege(:role, 'playlist_sources_id_seq', 'USAGE')"),
            {"role": PIPELINE},
        )

    def test_cannot_edit_migration_history(self, roles):
        assert can(roles, PIPELINE, "alembic_version", "SELECT")
        assert not can(roles, PIPELINE, "alembic_version", "UPDATE")

    def test_cannot_change_schema(self, roles):
        assert not roles.scalar(
            text("select has_schema_privilege(:role, 'public', 'CREATE')"), {"role": PIPELINE}
        )


def test_granting_twice_is_safe(roles):
    grant_privileges(roles.connection(), web_role=WEB, pipeline_role=PIPELINE)

    assert can(roles, WEB, "profiles", "INSERT")


def test_missing_role_raises_clear_error(session):
    with pytest.raises(LookupError, match="noble_nobody"):
        grant_privileges(session.connection(), web_role="noble_nobody", pipeline_role="noble_nobody_either")


@pytest.mark.parametrize("bad_name", ["web; drop table profiles", "Web", "1web", "web-role", ""])
def test_unsafe_role_names_are_rejected(session, bad_name):
    with pytest.raises(ValueError, match="role name"):
        grant_privileges(session.connection(), web_role=bad_name, pipeline_role=PIPELINE)
