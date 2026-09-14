"""Settings for the whole app rather than one profile. For now: the nightly Claude budget.

Claude costs money every night, so the limit is Jarred's to set in the web app, not a constant
in the code. It starts at $2, his target, and is read when each night's research begins, so a
change made in the evening applies to that night. The settings row is created the first time
the budget is saved; until then the default applies.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.orm import Session

from core.models import AppSettings

SETTINGS_ROW_ID = 1
DEFAULT_NIGHTLY_CLAUDE_BUDGET_USD = Decimal("2.00")
MAX_NIGHTLY_CLAUDE_BUDGET_USD = Decimal("50.00")
CENTS = Decimal("0.01")
BUDGET_MESSAGE = "Enter a nightly budget between $0 and $50, for example 2 or 2.50"


class SettingsValidationError(ValueError):
    """One message per field, e.g. {"budget": "..."}."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(errors.values()))


def nightly_claude_budget(session: Session) -> Decimal:
    settings = session.get(AppSettings, SETTINGS_ROW_ID)
    return settings.nightly_claude_budget_usd if settings else DEFAULT_NIGHTLY_CLAUDE_BUDGET_USD


def set_nightly_claude_budget(session: Session, typed: str | Decimal) -> Decimal:
    budget = _parse_budget(typed)
    settings = session.get(AppSettings, SETTINGS_ROW_ID)
    if settings is None:
        settings = AppSettings(id=SETTINGS_ROW_ID)
        session.add(settings)
    settings.nightly_claude_budget_usd = budget
    session.flush()
    return budget


def _parse_budget(typed: str | Decimal) -> Decimal:
    text = str(typed).strip().removeprefix("$").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise SettingsValidationError({"budget": BUDGET_MESSAGE}) from None
    if not value.is_finite() or value < 0 or value > MAX_NIGHTLY_CLAUDE_BUDGET_USD:
        raise SettingsValidationError({"budget": BUDGET_MESSAGE})
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)
