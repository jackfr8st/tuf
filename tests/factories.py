"""Builders for fictional Splitwise API payloads used across tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tuf.models import ClassSource, LedgerEntry, SyncStatus, TxnType

ME = 1001
ALEX = 2002
SAM = 3003
JO = 4004


def user(uid: int, paid: str, owed: str, net: str | None = None) -> dict[str, Any]:
    from decimal import Decimal

    net_value = net if net is not None else str(Decimal(paid) - Decimal(owed))
    return {
        "user": {"id": uid, "first_name": "Fictional", "email": f"user{uid}@example.invalid"},
        "user_id": uid,
        "paid_share": paid,
        "owed_share": owed,
        "net_balance": net_value,
    }


def expense(
    eid: int,
    description: str,
    cost: str,
    users: list[dict[str, Any]],
    *,
    date: str = "2026-10-10T19:00:00Z",
    updated_at: str = "2026-10-10T19:05:00Z",
    currency: str = "USD",
    group_id: int | None = None,
    payment: bool = False,
    deleted_at: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    return {
        "id": eid,
        "group_id": group_id,
        "description": description,
        "payment": payment,
        "cost": cost,
        "currency_code": currency,
        "date": date,
        "created_at": updated_at,
        "updated_at": updated_at,
        "deleted_at": deleted_at,
        "category": {"id": 18, "name": category or "General"},
        "users": users,
        "repayments": [],
    }


def entry(**kw):
    base = dict(
        sync_key="sw:1:1",
        expense_id=1,
        account_id=1,
        title="X",
        description="X",
        date=date(2026, 10, 1),
        currency="USD",
        total_cost=Decimal("10"),
        paid_share=Decimal("10"),
        owed_share=Decimal("10"),
        my_share=Decimal("10"),
        net_contribution=Decimal("0"),
        txn_type=TxnType.EXPENSE,
        status=SyncStatus.ACTIVE,
        spending=Decimal("10"),
        fx_rate=None,
        fx_date=None,
        primary="Other",
        subcategory=None,
        suggested_subcategory=None,
        merchant=None,
        group_name="Non-group",
        group_id=None,
        trip_key=None,
        class_source=ClassSource.FALLBACK,
        confidence=Decimal("0"),
        locked=False,
        class_key="",
        source_updated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    base.update(kw)
    return LedgerEntry(**base)


class FakeAI:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def classify(self, request):
        self.calls.append(request)
        if self.exc:
            raise self.exc
        if callable(self.response):
            return self.response(request)
        return self.response


def ai_ok(
    primary="Dining Out",
    sub=None,
    suggest=False,
    conf=0.9,
    title="Dinner at Zuni",
    merchant="Zuni",
    trip=None,
):
    def respond(request):
        return [
            {
                "ref": i.ref,
                "clean_title": title,
                "primary_category": primary,
                "subcategory": sub,
                "merchant": merchant,
                "trip_tag": trip,
                "confidence": conf,
                "suggest_new_subcategory": suggest,
            }
            for i in request.items
        ]

    return respond
