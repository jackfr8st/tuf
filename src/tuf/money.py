"""Exact decimal money helpers.

All financial amounts in TUF are ``decimal.Decimal``. Floats only appear at the Notion
boundary (JSON numbers), and are converted with ``Decimal(str(x))`` so that the shortest
round-trip representation is preserved (``12.34`` stays ``12.34``).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

ZERO = Decimal("0")
CENT = Decimal("0.01")

# ISO 4217 currencies with no minor unit. Everything else is quantized to cents.
_ZERO_DECIMAL = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "ISK",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}


class MoneyError(ValueError):
    """Raised when an amount cannot be parsed exactly."""


def parse_amount(value: object) -> Decimal:
    """Parse an amount from a Splitwise string / Notion number without float error."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):
        raise MoneyError(f"boolean is not an amount: {value!r}")
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal amount: {value!r}") from exc
    else:
        raise MoneyError(f"unsupported amount type: {type(value).__name__}")
    if not result.is_finite():
        raise MoneyError(f"non-finite amount: {value!r}")
    return result


def minor_unit(currency: str) -> Decimal:
    return Decimal("1") if currency.upper() in _ZERO_DECIMAL else CENT


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Round to the currency's minor unit using banker's rounding."""
    return amount.quantize(minor_unit(currency), rounding=ROUND_HALF_EVEN)


def to_json_number(amount: Decimal | None) -> float | int | None:
    """Convert for the Notion API. Values with <=15 significant digits round-trip exactly."""
    if amount is None:
        return None
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def fmt(amount: Decimal | None, currency: str = "") -> str:
    """Human formatting, e.g. ``$1,234.50`` or ``1,234.50 EUR``."""
    if amount is None:
        return "—"
    symbol = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "INR": "₹",
        "JPY": "¥",
        "CAD": "CA$",
        "AUD": "A$",
    }.get(currency.upper(), "")
    q = quantize(amount, currency or "USD")
    sign = "-" if q < 0 else ""
    places = 0 if minor_unit(currency or "USD") == 1 else 2
    body = f"{abs(q):,.{places}f}"
    if symbol:
        return f"{sign}{symbol}{body}"
    return f"{sign}{body} {currency}".strip()


def pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Percentage with 1 decimal place, or None when the denominator is not positive."""
    if denominator <= 0:
        return None
    return (numerator / denominator * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
