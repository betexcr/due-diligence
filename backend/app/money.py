"""Monetary values are integers in minor units: hundredths of the currency unit.

One fixed scale (100) for every currency keeps arithmetic exact and the
representation uniform across storage, rules and the API. Floats appear only
where the LLM contract hands us a printed number, and are converted here via
Decimal(str(x)) so 0.1 + 0.2 style binary errors never reach the database.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

MINOR_PER_UNIT = 100


def to_minor(value: float | int | str | Decimal, multiplier: int = 1) -> int:
    """Printed value x unit multiplier -> integer minor units, banker's rounding."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite():
        raise ValueError(f"Non-finite monetary value: {value!r}")
    return int((d * multiplier * MINOR_PER_UNIT).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def format_minor(minor: int, currency: str | None = None) -> str:
    """12_500_000_00 -> '12,500,000'; keeps cents only when non-zero. Negatives in parentheses."""
    units, cents = divmod(abs(minor), MINOR_PER_UNIT)
    s = f"{units:,}" + (f".{cents:02d}" if cents else "")
    if minor < 0:
        s = f"({s})"
    return f"{currency} {s}" if currency else s
