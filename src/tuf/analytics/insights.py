"""Factual, deterministic insights. Every sentence is derived from computed totals; nothing
here comes from an AI model. Projections are always labelled as projections."""

from __future__ import annotations

from decimal import Decimal

from tuf.analytics.engine import Dashboard, Insight
from tuf.money import ZERO, fmt

MIN_COMPARABLE = Decimal("20")  # ignore % changes on tiny amounts


def generate_insights(d: Dashboard) -> list[Insight]:
    cur = d.currency
    out: list[Insight] = []
    m = d.month
    days_left = m.days_in_month - m.elapsed_days

    if m.budget.limit:
        level = {"Green": "good", "Amber": "warn", "Red": "alert", "Over": "alert"}[m.budget.status]
        out.append(
            Insight(
                level,
                f"You've used {m.budget.utilization}% of your "
                f"{fmt(m.budget.limit, cur)} monthly budget with {days_left} "
                f"day{'s' if days_left != 1 else ''} left.",
            )
        )
        if m.projection_is_estimate and m.elapsed_days >= 3:
            if m.projected > m.budget.limit:
                out.append(
                    Insight(
                        "warn",
                        "At the current pace, spending may reach about "
                        f"{fmt(m.projected, cur)} this month (projection), "
                        f"{fmt(m.projected - m.budget.limit, cur)} over budget.",
                    )
                )
            else:
                out.append(
                    Insight(
                        "good",
                        "At the current pace you're projected to finish "
                        f"around {fmt(m.projected, cur)}, within budget (projection).",
                    )
                )

    for line in d.budgets[1:]:
        if line.status == "Over":
            out.append(
                Insight(
                    "alert",
                    f"{line.name} is over its budget by "
                    f"{fmt(-(line.remaining or ZERO), cur)} "
                    f"({line.utilization}% used).",
                )
            )
        elif line.status in ("Red", "Amber"):
            out.append(
                Insight(
                    "warn" if line.status == "Amber" else "alert",
                    f"{line.name} has consumed {line.utilization}% of its monthly budget.",
                )
            )

    increases = [
        (c, now, prev)
        for c, now, prev in d.mom
        if prev >= MIN_COMPARABLE and now >= MIN_COMPARABLE and now > prev
    ]
    increases.sort(key=lambda x: -(x[1] - x[2]))
    for cat, now, prev in increases[:2]:
        change = ((now - prev) / prev * 100).quantize(Decimal("1"))
        if change >= 15:
            out.append(
                Insight(
                    "info",
                    f"{cat} spending is up {change}% versus the same point "
                    f"last month ({fmt(now, cur)} vs {fmt(prev, cur)}).",
                )
            )
    decreases = [(c, now, prev) for c, now, prev in d.mom if prev >= MIN_COMPARABLE and now < prev]
    decreases.sort(key=lambda x: -(x[2] - x[1]))
    for cat, now, prev in decreases[:1]:
        out.append(
            Insight(
                "good",
                f"{cat} spending is down {fmt(prev - now, cur)} versus the same point last month.",
            )
        )

    cats = {c.name: c.amount for c in d.categories}
    dining, groceries = cats.get("Dining Out", ZERO), cats.get("Groceries", ZERO)
    if dining > 0 and groceries > 0:
        ratio = (dining / groceries).quantize(Decimal("0.1"))
        out.append(
            Insight(
                "info",
                f"Dining Out ({fmt(dining, cur)}) is {ratio}× Groceries "
                f"({fmt(groceries, cur)}) this month.",
            )
        )
    if d.categories and m.spent > 0 and d.categories[0].share_pct:
        top = d.categories[0]
        out.append(
            Insight(
                "info",
                f"{top.name} is your largest category this month at "
                f"{top.share_pct}% of spending ({fmt(top.amount, cur)}).",
            )
        )

    for day, amount in d.spikes[:2]:
        out.append(
            Insight(
                "warn",
                f"Spending spike on {day:%b %d}: {fmt(amount, cur)} "
                f"(a typical day is about {fmt(d.typical_day, cur)}).",
            )
        )
    for merchant, count, total in d.frequent[:2]:
        out.append(
            Insight(
                "info", f"{merchant}: {count} purchases this month totalling {fmt(total, cur)}."
            )
        )
    if d.recurring:
        total = sum((r.monthly_average for r in d.recurring), ZERO)
        names = ", ".join(r.merchant for r in d.recurring[:4])
        out.append(
            Insight(
                "info", f"Recurring purchases ({names}) average about {fmt(total, cur)} per month."
            )
        )
    for trip in d.trips:
        if trip.status == "Over":
            out.append(
                Insight(
                    "alert",
                    f"Trip '{trip.name}' is over budget: "
                    f"{fmt(trip.total, cur)} of {fmt(trip.budget, cur)}.",
                )
            )
    if m.excluded_unconverted:
        parts = ", ".join(f"{n} in {c}" for c, n in sorted(m.excluded_unconverted.items()))
        out.append(Insight("warn", f"Not included in totals (no exchange rate): {parts}."))
    if d.pending_classification:
        out.append(
            Insight(
                "info",
                f"{d.pending_classification} expense(s) are waiting for "
                "AI categorization and are shown under Other for now.",
            )
        )
    return out
