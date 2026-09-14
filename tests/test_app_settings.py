"""Settings for the whole app, edited in the web app. For now: the nightly Claude budget."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.app_settings import (
    DEFAULT_NIGHTLY_CLAUDE_BUDGET_USD,
    SettingsValidationError,
    nightly_claude_budget,
    set_nightly_claude_budget,
)
from core.models import AppSettings


def test_the_budget_starts_at_two_dollars(session):
    assert nightly_claude_budget(session) == Decimal("2.00")
    assert DEFAULT_NIGHTLY_CLAUDE_BUDGET_USD == Decimal("2.00")


@pytest.mark.parametrize(
    ("typed", "saved"),
    [
        ("3.50", Decimal("3.50")),
        ("$5", Decimal("5.00")),
        (" 0 ", Decimal("0.00")),
        ("1.234", Decimal("1.23")),
    ],
)
def test_setting_the_budget(session, typed, saved):
    assert set_nightly_claude_budget(session, typed) == saved
    assert nightly_claude_budget(session) == saved


def test_changing_it_again_updates_the_same_row(session):
    set_nightly_claude_budget(session, "3")
    set_nightly_claude_budget(session, "4")

    assert len(session.scalars(select(AppSettings)).all()) == 1
    assert nightly_claude_budget(session) == Decimal("4.00")


@pytest.mark.parametrize("typed", ["", "abc", "-1", "50.01", "NaN", "Infinity"])
def test_unreasonable_budgets_are_refused(session, typed):
    with pytest.raises(SettingsValidationError) as caught:
        set_nightly_claude_budget(session, typed)

    assert "between" in caught.value.errors["budget"]


def test_the_database_keeps_a_single_settings_row(session):
    session.add(AppSettings(id=2, nightly_claude_budget_usd=Decimal("1")))

    with pytest.raises(IntegrityError):
        session.flush()
