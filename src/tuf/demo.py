"""Fictional demo data run through the real (offline) pipeline.

Used by ``tuf demo`` to produce screenshots / a sample report without any credentials, and
to generate ``examples/sample_expenses.json``. All names, places and amounts are invented.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from tuf.analytics.engine import AnalyticsInput, Dashboard, build_dashboard
from tuf.categorization.engine import Categorizer, ClassifyInput
from tuf.categorization.taxonomy import Taxonomy
from tuf.finance.engine import compute_facts
from tuf.finance.fx import NoRates
from tuf.models import LedgerEntry
from tuf.splitwise.client import GroupInfo
from tuf.splitwise.parse import parse_expense
from tuf.sync.engine import group_name_for, make_entry
from tuf.trips.engine import TripDef, TripMatcher

ME, RIVER, SKY, ROWAN = 1, 2, 3, 4
GROUPS = {
    101: GroupInfo(101, "Apartment 4B", "apartment"),
    201: GroupInfo(201, "Trip: Big Sur 2026", "trip"),
    301: GroupInfo(301, "Weekend Crew", "other"),
}


def _u(uid: int, paid: Decimal, owed: Decimal) -> dict[str, Any]:
    return {
        "user_id": uid,
        "paid_share": f"{paid:.2f}",
        "owed_share": f"{owed:.2f}",
        "net_balance": f"{paid - owed:.2f}",
    }


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 - fictional demo data, not security
        self.items: list[dict[str, Any]] = []
        self.next_id = 5000

    def add(
        self,
        day: date,
        desc: str,
        cost: str,
        people: list[int],
        payer: int = ME,
        *,
        group: int | None = None,
        payment: bool = False,
        currency: str = "USD",
        deleted: bool = False,
        category: str = "General",
    ) -> None:
        total = Decimal(cost)
        n = len(people)
        base = (total / n).quantize(Decimal("0.01"))
        shares = [base] * n
        shares[0] += total - base * n  # payer-side rounding remainder, sums exactly
        users = [
            _u(uid, total if uid == payer else Decimal(0), shares[i])
            for i, uid in enumerate(people)
        ]
        if payer not in people:
            users.append(_u(payer, total, Decimal(0)))
        stamp = datetime(day.year, day.month, day.day, 19, 0, tzinfo=UTC)
        self.next_id += 1
        self.items.append(
            {
                "id": self.next_id,
                "group_id": group,
                "description": desc,
                "payment": payment,
                "cost": f"{total:.2f}",
                "currency_code": currency,
                "date": stamp.isoformat().replace("+00:00", "Z"),
                "created_at": stamp.isoformat().replace("+00:00", "Z"),
                "updated_at": (stamp + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "deleted_at": (stamp + timedelta(days=1)).isoformat().replace("+00:00", "Z")
                if deleted
                else None,
                "category": {"id": 0, "name": category},
                "users": users,
            }
        )

    def money(self, lo: float, hi: float) -> str:
        return f"{self.rng.uniform(lo, hi):.2f}"


def demo_expenses(today: date = date(2026, 10, 24), seed: int = 7) -> list[dict[str, Any]]:
    g = _Gen(seed)
    start = date(today.year, today.month, 1) - timedelta(days=150)
    day = start
    while day <= today:
        wd = day.weekday()
        if wd in (0, 2, 4) and g.rng.random() < 0.8:
            g.add(
                day,
                g.rng.choice(["Starbucks latte", "Blue Bottle coffee", "Cafe cortado"]),
                g.money(4.5, 7.5),
                [ME],
            )
        if wd == 6:
            store = g.rng.choice(["Trader Joe's", "Ralphs groceries", "Target run", "Costco"])
            payer = g.rng.choice([ME, RIVER])
            g.add(day, store, g.money(45, 140), [ME, RIVER], payer, group=101)
        if wd in (4, 5) and g.rng.random() < 0.7:
            place = g.rng.choice(
                [
                    "Dinner at Lucia's Trattoria",
                    "Ramen Nakamura",
                    "Taco night",
                    "Brunch at Sunny Side",
                    "Sushi Hana",
                ]
            )
            g.add(
                day,
                place,
                g.money(48, 160),
                [ME, SKY, ROWAN],
                g.rng.choice([ME, SKY, ROWAN]),
                group=301,
            )
        if g.rng.random() < 0.18:
            g.add(
                day,
                g.rng.choice(["Uber to campus", "Lyft home", "Waymo downtown"]),
                g.money(9, 28),
                [ME],
            )
        if g.rng.random() < 0.06:
            g.add(
                day, g.rng.choice(["Chips and salsa", "Snacks from 7-Eleven"]), g.money(3, 9), [ME]
            )
        if g.rng.random() < 0.05:
            g.add(
                day,
                g.rng.choice(["Uniqlo jacket", "Running shoes", "Phone case"]),
                g.money(20, 85),
                [ME],
            )
        if day.day == 1:
            g.add(day, "Rent", "1450.00", [ME, RIVER], RIVER, group=101, category="Rent")
            g.add(day, "Health insurance", "118.00", [ME])
        if day.day == 3:
            g.add(day, "Netflix", "15.49", [ME, RIVER], ME, group=101)
            g.add(day, "Spotify", "11.99", [ME])
        if day.day == 6:
            g.add(
                day,
                "Internet bill",
                "70.00",
                [ME, RIVER],
                ME,
                group=101,
                category="TV/Phone/Internet",
            )
        if day.day == 28:
            g.add(day, "Payment", g.money(60, 140), [RIVER], ME, payment=True)
        day += timedelta(days=1)

    trip = date(today.year, today.month, 9)
    g.add(trip, "Gas on Highway 1", "64.20", [ME, SKY, ROWAN], SKY, group=201)
    g.add(trip, "Airbnb cabin", "540.00", [ME, SKY, ROWAN], ME, group=201)
    g.add(
        trip + timedelta(days=1), "Dinner at Nepenthe", "186.00", [ME, SKY, ROWAN], ROWAN, group=201
    )
    g.add(trip + timedelta(days=1), "Kayak rental", "90.00", [ME, SKY, ROWAN], ME, group=201)
    g.add(
        trip + timedelta(days=2), "Coffee and pastries", "27.50", [ME, SKY, ROWAN], SKY, group=201
    )
    tahoe = date(today.year, today.month, 1) - timedelta(days=70)
    g.add(tahoe, "Cabin deposit #tahoe2026", "300.00", [ME, ROWAN], ROWAN)
    g.add(tahoe + timedelta(days=1), "Groceries #tahoe2026", "84.00", [ME, ROWAN], ME)
    g.add(tahoe + timedelta(days=2), "Bowling #tahoe2026", "48.00", [ME, ROWAN], ME)
    g.add(today - timedelta(days=3), "Birthday gift for Sky", "40.00", [SKY], ME, group=301)
    g.add(today - timedelta(days=5), "Tapas in Lisbon", "36.00", [ME, ROWAN], ROWAN, currency="EUR")
    g.add(today - timedelta(days=2), "Concert tickets", "120.00", [ME, SKY], ME, deleted=True)
    return g.items


def demo_dashboard(
    today: date = date(2026, 10, 24),
) -> tuple[Dashboard, list[LedgerEntry], Taxonomy]:
    raw = demo_expenses(today)
    tax = Taxonomy.defaults()
    for name, budget in {
        "Dining Out": "320",
        "Groceries": "260",
        "Entertainment": "60",
        "Transportation": "150",
    }.items():
        tax.primaries[name].monthly_budget = Decimal(budget)
    trips = [TripDef("Big Sur 2026", budget=Decimal("450"), group_refs={"201"})]
    matcher = TripMatcher(trips)
    cat = Categorizer(tax)
    ledger: list[LedgerEntry] = []
    for item in raw:
        exp = parse_expense(item)
        facts = compute_facts(exp, ME)
        if facts is None:
            continue
        cls = cat.classify_without_ai(ClassifyInput("x", exp.description, exp.splitwise_category))
        assert cls is not None
        gname = group_name_for(exp.group_id, GROUPS)
        match = matcher.match(exp.description, exp.group_id, gname if exp.group_id else None)
        ledger.append(
            make_entry(
                exp,
                facts,
                cls,
                ME,
                gname,
                match.key if match else None,
                None,
                "USD",
                UTC,
                NoRates(),
            )
        )
    budgets = {n: c.monthly_budget for n, c in tax.primaries.items() if c.monthly_budget}
    dash = build_dashboard(
        AnalyticsInput(
            ledger,
            today,
            "USD",
            Decimal("1500"),
            budgets,
            {},
            list(matcher.trips.values()),
            group_names={g.id: g.name for g in GROUPS.values()},
        )
    )
    return dash, ledger, tax
