"""Optional currency conversion into the reporting currency.

Source: Frankfurter (https://frankfurter.dev), free, no API key, open source, blended
central-bank reference rates. The rate and the rate date actually returned are stored on
every converted expense. If no rate is available the amount is left unconverted
(``None``) instead of guessing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from tuf.http import ApiError, HttpClient
from tuf.money import quantize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal
    date: date
    source: str


class RateProvider(Protocol):
    def rate(self, base: str, quote: str, on: date) -> FxRate | None: ...


class NoRates:
    """Conversion disabled: foreign-currency amounts stay unconverted."""

    def rate(self, base: str, quote: str, on: date) -> FxRate | None:
        return None


class FrankfurterRates:
    BASE_URL = "https://api.frankfurter.dev"

    def __init__(self, http: HttpClient | None = None, today: date | None = None) -> None:
        self._http = http or HttpClient("Frankfurter", self.BASE_URL, {}, min_interval=0.2)
        self._cache: dict[tuple[str, str, date], FxRate | None] = {}
        self._today = today or date.today()
        self._failed = False

    def rate(self, base: str, quote: str, on: date) -> FxRate | None:
        base, quote = base.upper(), quote.upper()
        on = min(on, self._today)
        key = (base, quote, on)
        if key in self._cache:
            return self._cache[key]
        if self._failed:
            return None
        try:
            data = self._http.request(
                "GET", f"/v2/rate/{base.lower()}/{quote.lower()}", params={"date": on.isoformat()}
            )
            raw = json.loads(json.dumps(data), parse_float=Decimal)
            result: FxRate | None = FxRate(
                base,
                quote,
                Decimal(raw["rate"]),
                date.fromisoformat(raw["date"]),
                "frankfurter.dev",
            )
        except ApiError as exc:
            if exc.status in (404, 422):
                result = None  # unsupported currency: permanent, cache it
            else:
                log.warning("FX lookup unavailable (%s); leaving amounts unconverted", exc)
                self._failed = True
                return None
        except (KeyError, ValueError, TypeError):
            log.warning("FX response malformed for %s->%s", base, quote)
            result = None
        self._cache[key] = result
        return result


def convert(
    amount: Decimal, currency: str, reporting: str, on: date, rates: RateProvider
) -> tuple[Decimal | None, FxRate | None]:
    """Convert ``amount`` to the reporting currency. Returns (converted, rate_used)."""
    if currency.upper() == reporting.upper():
        return amount, None
    if amount == 0:
        return Decimal("0"), None
    fx = rates.rate(currency, reporting, on)
    if fx is None:
        return None, None
    return quantize(amount * fx.rate, reporting), fx
