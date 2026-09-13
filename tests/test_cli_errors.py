"""Database errors shown by the CLI: specific enough to diagnose, never leaking secrets.

The first `create-roles` run on Neon failed with only "InternalError", hiding the real
cause ("Neon only supports being given plaintext passwords"). Errors now show the
SQLSTATE and the first line of the server's message, with anything password-like masked.
"""

from sqlalchemy.exc import DBAPIError

from pipeline.cli import describe_db_error


class FakeDriverError(Exception):
    def __init__(self, message: str, sqlstate: str | None):
        super().__init__(message)
        self.sqlstate = sqlstate


def wrap(message: str, sqlstate: str | None = "XX000") -> DBAPIError:
    return DBAPIError(
        statement="CREATE ROLE web WITH LOGIN PASSWORD 'hunter2secret'",
        params=None,
        orig=FakeDriverError(message, sqlstate),
    )


def test_includes_sqlstate_and_first_line_of_message():
    error = wrap(
        "Received HTTP code 400 from control plane: plaintext only\nDETAIL: more\nLINE 1: CREATE ROLE"
    )

    described = describe_db_error(error)

    assert "XX000" in described
    assert "plaintext only" in described
    assert "DETAIL" not in described
    assert "LINE 1" not in described


def test_never_includes_the_statement_or_its_password():
    described = describe_db_error(wrap("permission denied for table playlists", "42501"))

    assert "hunter2secret" not in described
    assert "CREATE ROLE" not in described


def test_masks_passwords_and_urls_that_appear_in_the_message():
    message = (
        "bad thing PASSWORD 'topsecret1' near SCRAM-SHA-256$4096:abc$def:ghi "
        "for postgresql://user:urlsecret@host/db and npg_AbCdEf123"
    )

    described = describe_db_error(wrap(message))

    for secret in ("topsecret1", "4096:abc$def:ghi", "urlsecret", "npg_AbCdEf123"):
        assert secret not in described


def test_handles_errors_without_a_sqlstate():
    described = describe_db_error(wrap("connection refused", sqlstate=None))

    assert "connection refused" in described
    assert "FakeDriverError" in described
