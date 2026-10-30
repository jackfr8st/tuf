"""Deterministic analytics over synchronized ledger entries.

Only ``LedgerEntry.counted`` entries contribute to spending (active, consumption-type, with
an amount in the reporting currency). Settlements, money advanced for others, deleted and
removed expenses never count as spending. Every number here is Decimal arithmetic over
stored amounts; no AI is involved.
"""

from __future__ import annotations

import calendar
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from tuf.categorization.text import fingerprint
from tuf.models import LedgerEntry
from tuf.money import ZERO, pct
from tuf.trips.engine import TripDef


@dataclass(frozen=True)
class Thresholds:
    amber: Decimal = Decimal("75")
    red: Decimal = Decimal("90")


def budget_status(utilization: Decimal | None, t: Thresholds) -> str:
    if utilization is None:
        return "No budget"
    if utilization > 100:
        return "Over"
    if utilization >= t.red:
        return "Red"
    if utilization >= t.amber:
        return "Amber"
    return "Green"


STATUS_EMOJI = {"Green": "🟢", "Amber": "🟡", "Red": "🔴", "Over": "⛔", "No budget": "⚪"}


def gauge(utilization: Decimal | None, cells: int = 10) -> str:
    """Text gauge for Notion. Caps the bar at 100% and states the overflow explicitly."""
    if utilization is None:
        return "no budget set"
    filled = int(min(Decimal(cells), max(ZERO, utilization / 100 * cells)))
    bar = "█" * filled + "░" * (cells - filled)
    if utilization > 100:
        return f"{bar} {utilization}% (over by {utilization - 100}%)"
    return f"{bar} {utilization}%"


@dataclass
class BudgetLine:
    name: str
    category: str | None  # None => overall
    limit: Decimal | None
    actual: Decimal
    remaining: Decimal | None
    utilization: Decimal | None
    status: str
    parent: str | None = None

    @property
    def gauge(self) -> str:
        return gauge(self.utilization)


@dataclass
class CategoryTotal:
    name: str
    amount: Decimal
    share_pct: Decimal | None
    count: int
    budget: BudgetLine | None
    subcategories: list[tuple[str, Decimal]] = field(default_factory=list)


@dataclass
class MonthSummary:
    month: str
    spent: Decimal
    budget: BudgetLine
    daily_average: Decimal
    projected: Decimal
    projection_is_estimate: bool
    elapsed_days: int
    days_in_month: int
    expense_count: int
    excluded_unconverted: dict[str, int]
    advanced_for_others: Decimal  # money paid on others' behalf this month (reporting ccy)
    settlements_paid: Decimal
    settlements_received: Decimal


@dataclass
class TripSummary:
    key: str
    name: str
    start: date | None
    end: date | None
    budget: Decimal | None
    total: Decimal
    utilization: Decimal | None
    status: str
    count: int
    by_category: list[tuple[str, Decimal]]
    daily: list[tuple[date, Decimal]]
    largest: list[tuple[str, date, Decimal]]
    groups: list[str]
    page_id: str | None = None


@dataclass
class Recurring:
    merchant: str
    category: str
    months: int
    monthly_average: Decimal
    last_date: date


@dataclass
class Insight:
    level: str  # "info" | "warn" | "alert" | "good"
    text: str


@dataclass
class Dashboard:
    as_of: date
    currency: str
    month: MonthSummary
    categories: list[CategoryTotal]
    budgets: list[BudgetLine]
    daily: list[tuple[date, Decimal]]
    cumulative: list[tuple[date, Decimal]]
    weekly: list[tuple[date, Decimal]]
    monthly: list[tuple[str, Decimal]]
    mom: list[tuple[str, Decimal, Decimal]]  # category, this MTD, last month same period
    spikes: list[tuple[date, Decimal]]
    typical_day: Decimal
    trips: list[TripSummary]
    recurring: list[Recurring]
    frequent: list[tuple[str, int, Decimal]]
    insights: list[Insight]
    recent: list[LedgerEntry]
    net_balance: Decimal
    pending_classification: int


def _amt(e: LedgerEntry) -> Decimal:
    """Reporting-currency spending of a counted entry (never None for counted entries)."""
    return e.spending if e.spending is not None else ZERO


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add_months(d: date, months: int) -> date:
    idx = d.year * 12 + d.month - 1 + months
    return date(idx // 12, idx % 12 + 1, 1)


@dataclass
class AnalyticsInput:
    entries: list[LedgerEntry]
    today: date
    currency: str
    overall_budget: Decimal | None
    category_budgets: dict[str, Decimal]
    subcategory_budgets: dict[tuple[str, str], Decimal]
    trips: list[TripDef]
    thresholds: Thresholds = field(default_factory=Thresholds)
    group_names: dict[int, str] = field(default_factory=dict)


def build_dashboard(inp: AnalyticsInput) -> Dashboard:
    today, t = inp.today, inp.thresholds
    counted = [e for e in inp.entries if e.counted]
    this_month = month_key(today)
    month_entries = [e for e in counted if month_key(e.date) == this_month]

    # ---- month summary -------------------------------------------------------------
    spent = _q(sum((_amt(e) for e in month_entries), ZERO))
    dim = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day
    daily_avg = _q(spent / elapsed)
    projected = _q(spent / elapsed * dim)
    overall = _budget_line("Overall", None, inp.overall_budget, spent, t)

    unconverted: Counter[str] = Counter(
        e.currency
        for e in inp.entries
        if e.status.value == "Needs FX" and month_key(e.date) == this_month
    )
    active_month = [
        e
        for e in inp.entries
        if e.status.value == "Active"
        and month_key(e.date) == this_month
        and e.currency == inp.currency
    ]
    advanced = _q(
        sum(
            (
                e.net_contribution
                for e in active_month
                if not e.txn_type.is_settlement and e.net_contribution > 0
            ),
            ZERO,
        )
    )
    s_paid = _q(
        sum((e.paid_share for e in active_month if e.txn_type.value == "Settlement Paid"), ZERO)
    )
    s_recv = _q(
        sum((e.owed_share for e in active_month if e.txn_type.value == "Settlement Received"), ZERO)
    )
    month = MonthSummary(
        this_month,
        spent,
        overall,
        daily_avg,
        projected,
        elapsed < dim,
        elapsed,
        dim,
        len(month_entries),
        dict(unconverted),
        advanced,
        s_paid,
        s_recv,
    )

    # ---- categories & budgets ------------------------------------------------------
    by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_sub: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    counts: Counter[str] = Counter()
    for e in month_entries:
        by_cat[e.primary] += _amt(e)
        counts[e.primary] += 1
        by_sub[(e.primary, e.subcategory or "Unspecified")] += _amt(e)
    budgets: list[BudgetLine] = [overall]
    categories: list[CategoryTotal] = []
    all_cats = set(by_cat) | set(inp.category_budgets)
    for name in sorted(all_cats, key=lambda c: (-by_cat.get(c, ZERO), c)):
        amount = _q(by_cat.get(name, ZERO))
        line = None
        if name in inp.category_budgets:
            line = _budget_line(name, name, inp.category_budgets[name], amount, t)
            budgets.append(line)
        subs = sorted(
            ((s, _q(v)) for (p, s), v in by_sub.items() if p == name), key=lambda x: -x[1]
        )
        categories.append(
            CategoryTotal(
                name, amount, pct(amount, spent) if spent > 0 else None, counts[name], line, subs
            )
        )
    for (parent, sub), limit in sorted(inp.subcategory_budgets.items()):
        amount = _q(by_sub.get((parent, sub), ZERO))
        line = _budget_line(f"{parent} › {sub}", sub, limit, amount, t)
        line.parent = parent
        budgets.append(line)

    # ---- trends --------------------------------------------------------------------
    per_day: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for e in counted:
        per_day[e.date] += _amt(e)
    first = date(today.year, today.month, 1)
    daily = [
        (first + timedelta(days=i), _q(per_day.get(first + timedelta(days=i), ZERO)))
        for i in range(elapsed)
    ]
    running, cumulative = ZERO, []
    for d, v in daily:
        running += v
        cumulative.append((d, _q(running)))
    week0 = today - timedelta(days=today.weekday()) - timedelta(weeks=7)
    weekly = []
    for w in range(8):
        start = week0 + timedelta(weeks=w)
        total = sum((per_day.get(start + timedelta(days=i), ZERO) for i in range(7)), ZERO)
        weekly.append((start, _q(total)))
    per_month: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in counted:
        per_month[month_key(e.date)] += _amt(e)
    monthly = [
        (
            month_key(_add_months(first, -i)),
            _q(per_month.get(month_key(_add_months(first, -i)), ZERO)),
        )
        for i in range(11, -1, -1)
    ]

    # Month-over-month compares month-to-date against the same days of last month.
    prev_first = _add_months(first, -1)
    prev_dim = calendar.monthrange(prev_first.year, prev_first.month)[1]
    prev_cut = prev_first + timedelta(days=min(elapsed, prev_dim) - 1)
    prev_by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in counted:
        if prev_first <= e.date <= prev_cut:
            prev_by_cat[e.primary] += _amt(e)
    mom = sorted(
        (
            (c, _q(by_cat.get(c, ZERO)), _q(prev_by_cat.get(c, ZERO)))
            for c in set(by_cat) | set(prev_by_cat)
        ),
        key=lambda x: -x[1],
    )

    # Spikes ignore recurring bills (rent day is expected, not unusual).
    recurring = _recurring(counted, today)
    recurring_keys = {r.merchant for r in recurring}
    discretionary = [e for e in counted if _merchant_key(e) not in recurring_keys]
    disc_day: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for e in discretionary:
        disc_day[e.date] += _amt(e)
    spikes, typical = _spikes(disc_day, today, discretionary)

    # ---- trips, recurring, recent --------------------------------------------------
    trips = [_trip_summary(td, counted, t, inp.group_names) for td in inp.trips]
    trips = [s for s in trips if s.count > 0 or s.budget]
    trips.sort(key=lambda s: s.end or s.start or date.min, reverse=True)
    frequent = _frequent(month_entries)
    recent = sorted(
        (e for e in inp.entries if e.status.value == "Active"),
        key=lambda e: (e.date, e.source_updated_at),
        reverse=True,
    )[:12]
    net = _q(
        sum(
            (
                e.net_contribution
                for e in inp.entries
                if e.status.value == "Active" and e.currency == inp.currency
            ),
            ZERO,
        )
    )
    pending = sum(
        1 for e in inp.entries if e.status.value == "Active" and e.class_source.value == "Pending"
    )

    dash = Dashboard(
        today,
        inp.currency,
        month,
        categories,
        budgets,
        daily,
        cumulative,
        weekly,
        monthly,
        mom,
        spikes,
        typical,
        trips,
        recurring,
        frequent,
        [],
        recent,
        net,
        pending,
    )
    from tuf.analytics.insights import generate_insights

    dash.insights = generate_insights(dash)
    return dash


def _budget_line(
    name: str, category: str | None, limit: Decimal | None, actual: Decimal, t: Thresholds
) -> BudgetLine:
    if limit is None or limit <= 0:
        return BudgetLine(name, category, None, actual, None, None, "No budget")
    util = pct(actual, limit)
    return BudgetLine(
        name, category, limit, actual, _q(limit - actual), util, budget_status(util, t)
    )


def _spikes(
    per_day: dict[date, Decimal], today: date, counted: list[LedgerEntry]
) -> tuple[list[tuple[date, Decimal]], Decimal]:
    """Days this month far above the typical day over the trailing 90 days."""
    if not counted:
        return [], ZERO
    earliest = min(e.date for e in counted)
    start = max(earliest, today - timedelta(days=89))
    span = (today - start).days + 1
    if span < 30:
        return [], ZERO
    values = [float(per_day.get(start + timedelta(days=i), ZERO)) for i in range(span)]
    mean = statistics.fmean(values)
    std = statistics.pstdev(values)
    threshold = max(mean + 2 * std, 2 * mean)
    first = date(today.year, today.month, 1)
    spikes = [
        (d, _q(v))
        for d, v in per_day.items()
        if first <= d <= today and v > 0 and float(v) > threshold
    ]
    spikes.sort(key=lambda x: -x[1])
    return spikes[:3], _q(Decimal(str(round(mean, 2))))


def _trip_summary(
    td: TripDef, counted: list[LedgerEntry], t: Thresholds, group_names: dict[int, str]
) -> TripSummary:
    items = [e for e in counted if e.trip_key == td.key]
    total = _q(sum((_amt(e) for e in items), ZERO))
    cats: dict[str, Decimal] = defaultdict(lambda: ZERO)
    days: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for e in items:
        cats[e.primary] += _amt(e)
        days[e.date] += _amt(e)
    util = pct(total, td.budget) if td.budget else None
    groups = sorted({group_names.get(e.group_id, e.group_name) for e in items if e.group_id})
    largest = sorted(((e.title, e.date, _amt(e)) for e in items), key=lambda x: -x[2])[:5]
    return TripSummary(
        key=td.key,
        name=td.name,
        start=td.start or (min(e.date for e in items) if items else None),
        end=td.end or (max(e.date for e in items) if items else None),
        budget=td.budget,
        total=total,
        utilization=util,
        status=budget_status(util, t) if td.budget else "No budget",
        count=len(items),
        by_category=sorted(((c, _q(v)) for c, v in cats.items()), key=lambda x: -x[1]),
        daily=sorted((d, _q(v)) for d, v in days.items()),
        largest=largest,
        groups=groups,
        page_id=td.page_id,
    )


def _merchant_key(e: LedgerEntry) -> str:
    return e.merchant or fingerprint(e.title).title()


def _recurring(counted: Iterable[LedgerEntry], today: date, min_months: int = 3) -> list[Recurring]:
    """Subscription-like spending: seen in >= ``min_months`` of the last 6 months, about once
    a month (<= 2 purchases per active month), with a stable monthly amount (max <= 1.3x min).
    Frequent-but-variable purchases (groceries, restaurants) are deliberately excluded."""
    window_start = _add_months(date(today.year, today.month, 1), -5)
    monthly: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    count: Counter[str] = Counter()
    cats: dict[str, Counter[str]] = defaultdict(Counter)
    last: dict[str, date] = {}
    for e in counted:
        if e.date < window_start or e.txn_type.value != "Expense":
            continue
        key = _merchant_key(e)
        if not key:
            continue
        monthly[key][month_key(e.date)] += _amt(e)
        count[key] += 1
        cats[key][e.primary] += 1
        last[key] = max(last.get(key, e.date), e.date)
    out = []
    for k, per_month in monthly.items():
        values = list(per_month.values())
        if len(values) < min_months or count[k] > 2 * len(values) or min(values) <= 0:
            continue
        if max(values) > min(values) * Decimal("1.3"):
            continue
        avg = _q(sum(values, ZERO) / len(values))
        out.append(Recurring(k, cats[k].most_common(1)[0][0], len(values), avg, last[k]))
    return sorted(out, key=lambda r: -r.monthly_average)


def _frequent(
    month_entries: list[LedgerEntry], min_count: int = 4
) -> list[tuple[str, int, Decimal]]:
    counts: Counter[str] = Counter()
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in month_entries:
        if e.merchant:
            counts[e.merchant] += 1
            totals[e.merchant] += _amt(e)
    return [(m, n, _q(totals[m])) for m, n in counts.most_common() if n >= min_count]
