"""What a Claude call cost, worked out from its token usage.

Prices are US dollars per million tokens (input, output), as published in September 2026.
Writing to the prompt cache costs 1.25× the input price and reading from it 0.1×. A model
that isn't listed is priced like the most expensive one here, so a typo can only make the
nightly budget stricter, never looser.
"""

from decimal import Decimal

MILLION = Decimal(1_000_000)
PRICES = {
    "claude-opus-5": (Decimal(5), Decimal(25)),
    "claude-sonnet-5": (Decimal(2), Decimal(10)),
    "claude-haiku-4-5": (Decimal(1), Decimal(5)),
}
MOST_EXPENSIVE = PRICES["claude-opus-5"]
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
CACHE_READ_MULTIPLIER = Decimal("0.1")


def cost_of(model: str, usage) -> Decimal:
    input_price, output_price = _prices_for(model)
    cost = (
        _tokens(usage, "input_tokens") * input_price
        + _tokens(usage, "output_tokens") * output_price
        + _tokens(usage, "cache_creation_input_tokens") * input_price * CACHE_WRITE_MULTIPLIER
        + _tokens(usage, "cache_read_input_tokens") * input_price * CACHE_READ_MULTIPLIER
    )
    return cost / MILLION


def _prices_for(model: str) -> tuple[Decimal, Decimal]:
    for name, prices in PRICES.items():
        if model == name or model.startswith(f"{name}-"):  # dated snapshots, e.g. claude-haiku-4-5-20251001
            return prices
    return MOST_EXPENSIVE


def _tokens(usage, field: str) -> Decimal:
    return Decimal(getattr(usage, field, None) or 0)
