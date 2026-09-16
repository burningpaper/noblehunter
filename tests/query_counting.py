"""Counting the SQL a piece of code runs, so an N+1 shows up as a failing test.

The number itself is never the point: what these tests pin is that the count doesn't grow with
the number of rows. A page that costs two queries for three entries and two for thirty is fine;
one that costs one per entry is the bug worth catching.
"""

import contextlib

from sqlalchemy import event


@contextlib.contextmanager
def record_statements(session, into: list[str]):
    """Collect every statement this session's engine runs while the block is open."""

    def record(conn, cursor, statement, parameters, context, executemany):
        into.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", record)
