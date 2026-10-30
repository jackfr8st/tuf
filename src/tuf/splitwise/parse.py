"""Convert Splitwise API JSON into domain models, validating every field TUF depends on."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tuf.models import Share, SplitwiseExpense
from tuf.money import parse_amount


class SplitwiseParseError(ValueError):
    pass


def parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise SplitwiseParseError(f"invalid datetime: {value!r}")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SplitwiseParseError(f"invalid datetime: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_expense(raw: dict[str, Any]) -> SplitwiseExpense:
    try:
        expense_id = int(raw["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SplitwiseParseError("expense without a valid id") from exc

    try:
        shares = tuple(_parse_share(u) for u in raw.get("users") or [])
        currency = str(raw.get("currency_code") or "").upper()
        if not currency.isalpha() or len(currency) != 3:
            raise SplitwiseParseError(f"invalid currency_code {currency!r}")
        group_id = raw.get("group_id")
        category = raw.get("category")
        return SplitwiseExpense(
            id=expense_id,
            description=str(raw.get("description") or "").strip(),
            cost=parse_amount(raw.get("cost")),
            currency=currency,
            date=parse_datetime(raw.get("date")),
            updated_at=parse_datetime(raw.get("updated_at") or raw.get("created_at")),
            deleted_at=parse_datetime(raw["deleted_at"]) if raw.get("deleted_at") else None,
            group_id=int(group_id) if group_id else None,
            payment=bool(raw.get("payment")),
            shares=shares,
            splitwise_category=(
                str(category["name"])
                if isinstance(category, dict) and category.get("name")
                else None
            ),
        )
    except SplitwiseParseError as exc:
        raise SplitwiseParseError(f"expense {expense_id}: {exc}") from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise SplitwiseParseError(f"expense {expense_id}: {type(exc).__name__}: {exc}") from exc


def _parse_share(raw: dict[str, Any]) -> Share:
    user_id = raw.get("user_id")
    if user_id is None and isinstance(raw.get("user"), dict):
        user_id = raw["user"].get("id")
    if user_id is None:
        raise SplitwiseParseError("share without user id")
    net = raw.get("net_balance")
    return Share(
        user_id=int(user_id),
        paid_share=parse_amount(raw.get("paid_share")),
        owed_share=parse_amount(raw.get("owed_share")),
        net_balance=parse_amount(net) if net not in (None, "") else None,
    )
