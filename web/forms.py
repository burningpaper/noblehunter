"""Small shared helpers for reading posted form fields."""

from core.access import MAX_POSTGRES_INT


def form_id(raw: str) -> int:
    """A posted id, or 0 (which matches nothing) when it isn't a whole number Postgres can hold.

    An id above Postgres's `integer` range would make `session.get` raise a DataError and abort
    the transaction, so it's treated the same as any other unusable input: not found.
    """
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return 0
    value = int(text)
    return value if value <= MAX_POSTGRES_INT else 0
