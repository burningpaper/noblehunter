"""form_id: the shared parser for posted ids."""

import pytest

from core.access import MAX_POSTGRES_INT
from web.forms import form_id


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "x",
        "-1",
        "1.5",
        "²",
        str(MAX_POSTGRES_INT + 1),
        "99999999999",
    ],
)
def test_unusable_input_returns_zero(raw):
    assert form_id(raw) == 0


def test_the_largest_postgres_integer_is_accepted():
    assert form_id(str(MAX_POSTGRES_INT)) == MAX_POSTGRES_INT


def test_an_ordinary_id_round_trips():
    assert form_id(" 42 ") == 42
