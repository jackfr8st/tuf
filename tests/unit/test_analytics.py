"""Dashboard numbers must reconcile exactly with the ledger."""

from datetime import date, timedelta
from decimal import Decimal

from factories import entry
from tuf.analytics.engine import (
    AnalyticsInput,
    Thresholds,
    budget_status,
    build_dashboard,
    gauge,
)
from tuf.models import ClassSource, SyncStatus, TxnType
from tuf.trips.engine import TripDef

D = Decimal
TODAY = date(2026, 10, 15)


def e(i, amount, primary="Dining Out", day=TODAY, **kw):
    return entry(
        sync_key=f"k{i}",
        expense_id=i,
        date=day,
        spending=D(amount),
        my_share=D(amount),
        owed_share=D(amount),
        primary=primary,
        title=f"T{i}",
        **kw,
    )


def dash(entries, budget="1000", cats=None, trips=None, subs=None):
    return build_dashboard(
        AnalyticsInput(
            entries,
            TODAY,
            "USD",
            D(budget) if budget else None,
            cats or {},
            subs or {},
            trips or [],
        )
    )


def ledger():
    return [
        e(1, "30.00", "Dining Out"),
        e(2, "52.10", "Groceries", day=date(2026, 10, 3)),
        e(3, "25.00", "Stay", trip_key="bigsur2026"),
        e(4, "12.34", "Dining Out", trip_key="bigsur2026", subcategory="Coffee & Cafes"),
        e(5, "45.00", "Other", txn_type=TxnType.SETTLEMENT_PAID),  # not spending
        e(6, "99.00", "Shopping", status=SyncStatus.DELETED),  # archived
        e(7, "70.00", "Shopping", status=SyncStatus.REMOVED),
        e(8, "-10.00", "Shopping", txn_type=TxnType.REFUND),
        e(9, "200.00", "Dining Out", day=date(2026, 9, 10)),  # last month
        entry(
            sync_key="k10",
            expense_id=10,
            date=TODAY,
            currency="EUR",
            spending=None,
            status=SyncStatus.NEEDS_FX,
            primary="Dining Out",
        ),
    ]


def test_monthly_and_category_totals_reconcile():
    d = dash(ledger())
    assert d.month.spent == D("109.44")  # 30 + 52.10 + 25 + 12.34 - 10
    assert sum(c.amount for c in d.categories) == d.month.spent
    by = {c.name: c.amount for c in d.categories}
    assert by == {
        "Dining Out": D("42.34"),
        "Groceries": D("52.10"),
        "Stay": D("25.00"),
        "Shopping": D("-10.00"),
    }
    assert sum(v for _, v in d.daily) == d.month.spent
    assert d.cumulative[-1][1] == d.month.spent
    assert d.monthly[-1] == ("2026-10", d.month.spent)
    assert d.monthly[-2] == ("2026-09", D("200.00"))


def test_archived_settlements_and_unconverted_are_excluded():
    d = dash(ledger())
    assert d.month.expense_count == 5
    assert d.month.excluded_unconverted == {"EUR": 1}
    assert any("no exchange rate" in i.text for i in d.insights)


def test_trip_totals_reconcile_without_double_counting():
    d = dash(ledger(), trips=[TripDef("Big Sur 2026", budget=D("50"))])
    trip = d.trips[0]
    assert trip.total == D("37.34")
    assert trip.utilization == D("74.7")
    assert sum(v for _, v in trip.by_category) == trip.total
    # trip spending is also inside overall and category totals exactly once
    assert d.month.spent == D("109.44")


def test_budget_percentages_and_projection():
    d = dash(ledger(), budget="200", cats={"Dining Out": D("50"), "Stay": D("20")})
    assert d.month.budget.utilization == D("54.7")
    assert d.month.budget.remaining == D("90.56")
    assert d.month.daily_average == D("7.30")  # 109.44 / 15
    assert d.month.projected == D("226.18")  # 109.44 / 15 * 31
    assert d.month.projection_is_estimate
    lines = {b.name: b for b in d.budgets}
    assert lines["Dining Out"].utilization == D("84.7") and lines["Dining Out"].status == "Amber"
    assert lines["Stay"].status == "Over" and lines["Stay"].remaining == D("-5.00")
    texts = " ".join(i.text for i in d.insights)
    assert "projection" in texts and "Stay is over its budget by $5.00" in texts
    assert "Dining Out has consumed 84.7%" in texts


def test_subcategory_budget():
    d = dash(ledger(), subs={("Dining Out", "Coffee & Cafes"): D("10")})
    sub = next(b for b in d.budgets if b.category == "Coffee & Cafes")
    assert sub.actual == D("12.34") and sub.status == "Over"


def test_budget_status_thresholds_and_gauge():
    t = Thresholds()
    assert budget_status(D("74.9"), t) == "Green"
    assert budget_status(D("75"), t) == "Amber"
    assert budget_status(D("90"), t) == "Red"
    assert budget_status(D("100"), t) == "Red"
    assert budget_status(D("100.1"), t) == "Over"
    assert budget_status(None, t) == "No budget"
    assert gauge(D("250")).startswith("██████████ 250")  # capped bar, explicit overflow
    assert "over by 150" in gauge(D("250"))
    assert gauge(D("0")).startswith("░░░░░░░░░░")


def test_no_budget_means_no_gauge_or_budget_insight():
    d = dash(ledger(), budget=None)
    assert d.month.budget.status == "No budget"
    assert not any("monthly budget" in i.text for i in d.insights)


def test_month_over_month_uses_same_period():
    entries = [
        e(1, "100", "Groceries", day=date(2026, 10, 5)),
        e(2, "50", "Groceries", day=date(2026, 9, 5)),
        e(3, "500", "Groceries", day=date(2026, 9, 25)),
    ]  # after the 15th: excluded
    d = dash(entries)
    assert ("Groceries", D("100.00"), D("50.00")) in d.mom
    assert any("Groceries spending is up 100%" in i.text for i in d.insights)


def test_forecast_inputs_valid_on_first_day():
    d = build_dashboard(
        AnalyticsInput(
            [e(1, "31", day=date(2026, 10, 1))], date(2026, 10, 1), "USD", D("100"), {}, {}, []
        )
    )
    assert d.month.elapsed_days == 1
    assert d.month.projected == D("961.00")
    assert not any("projected" in i.text or "pace" in i.text for i in d.insights)


def test_recurring_and_frequent_merchants():
    entries = []
    for i, m in enumerate([date(2026, 8, 3), date(2026, 9, 3), date(2026, 10, 3)]):
        entries.append(e(i, "15.49", "Entertainment", day=m, merchant="Netflix"))
    for i in range(5):
        entries.append(e(100 + i, "5.25", day=TODAY - timedelta(days=i), merchant="Starbucks"))
    d = dash(entries)
    assert [(r.merchant, r.months, r.monthly_average) for r in d.recurring] == [
        ("Netflix", 3, D("15.49"))
    ]
    assert d.frequent == [("Starbucks", 5, D("26.25"))]


def test_spike_detection_needs_history():
    entries = [e(i, "10", day=TODAY - timedelta(days=i)) for i in range(60)]
    entries.append(e(999, "400", day=TODAY - timedelta(days=2)))
    d = dash(entries)
    assert d.spikes and d.spikes[0][1] == D("410.00")
    assert dash([e(1, "400")]).spikes == []


def test_net_balance_and_advanced_for_others():
    entries = [
        e(1, "30", net_contribution=D("90")),
        e(2, "25", net_contribution=D("-25")),
        e(3, "0", txn_type=TxnType.SETTLEMENT_PAID, net_contribution=D("45"), paid_share=D("45")),
    ]
    d = dash(entries)
    assert d.month.advanced_for_others == D("90.00")
    assert d.month.settlements_paid == D("45.00")
    assert d.net_balance == D("110.00")


def test_pending_classification_count():
    d = dash([e(1, "5", class_source=ClassSource.PENDING)])
    assert d.pending_classification == 1
