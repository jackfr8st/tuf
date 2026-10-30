"""End-to-end sync behaviour against an in-memory Notion and a mocked Splitwise API.

These are offline tests: no credentials, no network. They exercise the real Splitwise
client (pagination), finance engine, categorizer, trip matcher, Notion store/mapping,
analytics and dashboard renderer together.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from factories import ALEX, ME, SAM, FakeAI, ai_ok, expense, user
from fakes import FakeNotion, FakeSplitwiseServer, api_error
from tuf.categorization.ai import AIQuotaExceeded
from tuf.config import load_config
from tuf.finance.fx import FxRate
from tuf.notion import schema as S
from tuf.notion.mapping import p_select, r_number, r_select, r_text
from tuf.notion.store import WorkspaceError, setup_workspace
from tuf.splitwise.parse import parse_datetime
from tuf.sync.engine import SyncEngine, SyncOptions

D = Decimal
NOW = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)


def config(**extra):
    env = {
        "SPLITWISE_API_KEY": "sw-test-key",
        "NOTION_API_KEY": "ntn-test-key",
        "NOTION_ROOT_PAGE_ID": "0" * 32,
        "MONTHLY_BUDGET": "1000",
        "FX_ENABLED": "false",
        **extra,
    }
    return load_config(env)


class World:
    def __init__(self, expenses=None, groups=None, ai=None, rates=None, **cfg):
        self.notion = FakeNotion()
        self.server = FakeSplitwiseServer(ME, expenses or [], groups or [])
        self.cfg = config(**cfg)
        object.__setattr__(self.cfg, "notion_root_page_id", self.notion.root)
        setup_workspace(self.notion, self.notion.root, "USD")
        self.ai, self.rates, self.now = ai, rates, NOW

    def sync(self, **opts):
        engine = SyncEngine(
            self.cfg,
            self.server.client(),
            self.notion,
            ai=self.ai,
            rates=self.rates,
            clock=lambda: self.now,
        )
        summary, dash = engine.run(SyncOptions(**opts))
        return summary, dash

    def expense_rows(self):
        return {r_text(p["properties"], "Sync Key"): p for p in self.notion.rows(S.EXPENSES)}

    def row(self, eid):
        return self.expense_rows()[f"sw:{ME}:{eid}"]["properties"]


def basic_expenses():
    return [
        expense(
            1,
            "Dinner at Zuni",
            "120.00",
            [user(ME, "120.00", "30.00"), user(ALEX, "0", "30.00"), user(SAM, "0", "60.00")],
            date="2026-10-02T03:00:00Z",
            updated_at="2026-10-02T03:05:00Z",
        ),
        expense(
            2,
            "Hotel #bigsur2026",
            "100.00",
            [user(ALEX, "100.00", "75.00"), user(ME, "0", "25.00")],
            date="2026-10-05T19:00:00Z",
            updated_at="2026-10-05T19:05:00Z",
        ),
        expense(
            3,
            "Payment",
            "45.00",
            [user(ME, "45.00", "0"), user(ALEX, "0", "45.00")],
            payment=True,
            date="2026-10-06T19:00:00Z",
            updated_at="2026-10-06T19:05:00Z",
        ),
        expense(
            4,
            "Starbucks latte",
            "5.25",
            [user(ME, "5.25", "5.25")],
            date="2026-10-07T16:00:00Z",
            updated_at="2026-10-07T16:05:00Z",
        ),
        expense(
            5,
            "Their lunch",
            "30.00",
            [user(ALEX, "30.00", "15.00"), user(SAM, "0", "15.00")],
            date="2026-10-07T16:00:00Z",
            updated_at="2026-10-07T16:06:00Z",
        ),
    ]


def test_first_import_then_repeated_sync_is_idempotent():
    w = World(basic_expenses())
    s1, dash = w.sync()
    assert s1.mode == "full" and s1.ok
    assert (s1.fetched, s1.created, s1.not_involved) == (5, 4, 1)
    rows = w.expense_rows()
    assert len(rows) == 4
    assert r_number(w.row(1), "Spending") == D("30")
    assert r_number(w.row(1), "Net") == D("90")
    assert r_number(w.row(2), "Spending") == D("25")
    assert r_select(w.row(3), "Type") == "Settlement Paid"
    assert r_number(w.row(3), "Spending") == 0
    assert r_select(w.row(4), "Subcategory") == "Coffee & Cafes"
    assert dash.month.spent == D("60.25")

    writes_before = w.notion.writes
    s2, _ = w.sync()
    assert s2.mode == "incremental"
    assert (s2.created, s2.updated) == (0, 0)
    # 48h overlap from the Oct 7 checkpoint re-fetches #2-#5: 3 rows unchanged + 1 not involved
    assert (s2.fetched, s2.unchanged) == (4, 3)
    assert len(w.expense_rows()) == 4
    assert w.notion.writes == writes_before  # no expense, aggregate or dashboard writes


def test_trip_hashtag_creates_trip_and_links_once():
    w = World(basic_expenses())
    s, dash = w.sync()
    assert s.trips_created == 1
    trips = w.notion.rows(S.TRIPS)
    assert [r_text(t["properties"], "Name") for t in trips] == ["bigsur2026"]
    trip_id = trips[0]["id"]
    assert w.row(2)["Trip"]["relation"] == [{"id": trip_id}]
    assert r_number(trips[0]["properties"], "Spending Total") == D("25")
    assert dash.trips[0].total == D("25.00")
    w.sync()
    assert len(w.notion.rows(S.TRIPS)) == 1


def test_trip_group_name_pattern():
    groups = [{"id": 77, "name": "Trip: Big Sur 2026", "group_type": "trip"}]
    exps = [
        expense(
            1, "Gas", "60.00", [user(ME, "60.00", "20.00"), user(ALEX, "0", "40.00")], group_id=77
        )
    ]
    w = World(exps, groups)
    w.sync()
    assert r_select(w.row(1), "Group") == "Trip: Big Sur 2026"
    trip = w.notion.rows(S.TRIPS)[0]
    assert r_text(trip["properties"], "Name") == "Big Sur 2026"
    assert r_text(trip["properties"], "Splitwise Groups") == "77"


def test_updated_expense_is_recomputed():
    exps = basic_expenses()
    w = World(exps)
    w.sync()
    exps[0] = expense(
        1,
        "Dinner at Zuni",
        "160.00",
        [user(ME, "160.00", "40.00"), user(ALEX, "0", "40.00"), user(SAM, "0", "80.00")],
        date="2026-10-02T03:00:00Z",
        updated_at="2026-10-15T10:00:00Z",
    )
    w.server.expenses = exps
    s, dash = w.sync()
    assert (s.created, s.updated) == (0, 1)
    assert r_number(w.row(1), "Spending") == D("40")
    assert r_number(w.row(1), "Total Cost") == D("160")
    assert dash.month.spent == D("70.25")


def test_deleted_expense_soft_deleted_and_restorable():
    exps = basic_expenses()
    w = World(exps)
    w.sync()
    exps[3] = {
        **exps[3],
        "deleted_at": "2026-10-15T11:00:00Z",
        "updated_at": "2026-10-15T11:00:00Z",
    }
    s, dash = w.sync()
    assert s.deleted == 1
    assert r_select(w.row(4), "Status") == "Deleted"
    assert r_number(w.row(4), "Spending") == 0
    assert dash.month.spent == D("55.00")
    # undeleted in Splitwise
    exps[3] = {**exps[3], "deleted_at": None, "updated_at": "2026-10-15T12:00:00Z"}
    w.now = NOW + timedelta(hours=1)
    s, dash = w.sync()
    assert s.restored == 1 and r_select(w.row(4), "Status") == "Active"
    assert dash.month.spent == D("60.25")


def test_full_reconcile_marks_vanished_expenses_removed():
    exps = basic_expenses()
    w = World(exps)
    w.sync()
    w.server.expenses = [e for e in exps if e["id"] != 4]
    s, _ = w.sync(full=True)
    assert s.removed == 1
    assert r_select(w.row(4), "Status") == "Removed" and r_number(w.row(4), "Spending") == 0


def test_reconcile_refuses_mass_removal():
    exps = [
        expense(
            i,
            f"Coffee {i}",
            "3.00",
            [user(ME, "3.00", "3.00")],
            updated_at=f"2026-10-0{1 + i % 8}T10:00:00Z",
        )
        for i in range(1, 31)
    ]
    w = World(exps)
    w.sync()
    w.server.expenses = []  # e.g. an API glitch returning nothing
    s, _ = w.sync(full=True)
    assert s.removed == 0 and any("safety" in x for x in s.warnings)
    s2, _ = w.sync(full=True, force_reconcile=True)
    assert s2.removed == 30


def test_pagination_imports_everything():
    exps = [
        expense(
            i,
            f"Groceries {i}",
            "10.00",
            [user(ME, "10.00", "10.00")],
            updated_at=f"2026-10-{1 + i % 14:02d}T10:{i % 60:02d}:00Z",
        )
        for i in range(1, 26)
    ]
    w = World(exps)
    s, _ = w.sync()
    assert s.created == 25
    offsets = [
        int(r.url.params["offset"])
        for r in w.server.requests
        if r.url.path.endswith("get_expenses")
    ]
    assert offsets == [*range(0, 25, 3), 25]  # pages of 3 (last has 1), then empty


def test_splitwise_failure_aborts_before_any_notion_write():
    w = World(basic_expenses())
    writes = w.notion.writes
    w.server.fail_next = 401
    with pytest.raises(Exception, match="401"):
        w.sync()
    assert w.notion.writes == writes


def test_transient_notion_failure_then_recovery_without_duplicates():
    w = World(basic_expenses())
    calls = {"n": 0}

    def fail(op, **info):
        if op == "create_page" and "Sync Key" in info["properties"]:
            calls["n"] += 1
            if calls["n"] == 3:
                return api_error(503)
        return None

    w.notion.fail = fail
    s, _ = w.sync()
    assert s.aborted and not s.ok
    assert len(w.expense_rows()) == 2  # rows 1 and 2 written, stopped at the 3rd
    w.notion.fail = None
    s2, _ = w.sync()
    assert s2.ok
    assert len(w.expense_rows()) == 4  # interrupted run recovered, no duplicates


def test_permanent_row_error_is_reported_and_others_continue():
    w = World(basic_expenses())

    def fail(op, **info):
        props = info.get("properties") or {}
        if op == "create_page" and "sw:1001:2" in str(props.get("Sync Key")):
            return api_error(400, "validation_error")
        return None

    w.notion.fail = fail
    s, _ = w.sync()
    assert not s.aborted and len(s.write_errors) == 1
    assert len(w.expense_rows()) == 3


def test_concurrent_run_duplicates_are_healed():
    w = World(basic_expenses())
    w.sync()
    page = w.expense_rows()[f"sw:{ME}:4"]
    w.notion.create_page(
        page["parent"], {k: {v["type"]: v[v["type"]]} for k, v in page["properties"].items()}
    )
    assert (
        sum(
            1
            for p in w.notion.rows(S.EXPENSES)
            if r_text(p["properties"], "Sync Key") == f"sw:{ME}:4"
        )
        == 2
    )
    s, dash = w.sync()
    assert s.duplicates_trashed == 1
    assert (
        sum(
            1
            for p in w.notion.rows(S.EXPENSES)
            if r_text(p["properties"], "Sync Key") == f"sw:{ME}:4"
        )
        == 1
    )
    assert dash.month.spent == D("60.25")  # duplicate never double-counted


def test_missed_schedules_widen_the_window_automatically():
    exps = basic_expenses()
    w = World(exps)
    w.sync()
    # five days of missed runs; an expense created/updated in that gap
    exps.append(
        expense(
            9,
            "Uber home",
            "18.00",
            [user(ME, "18.00", "18.00")],
            date="2026-10-17T02:00:00Z",
            updated_at="2026-10-17T02:01:00Z",
        )
    )
    w.now = NOW + timedelta(days=5)
    s, _ = w.sync()
    assert s.mode == "incremental" and s.created == 1
    assert parse_datetime(s.window_start) <= datetime(2026, 10, 17, tzinfo=UTC)


def test_historical_backfill_since_date():
    exps = [
        *basic_expenses(),
        expense(
            50,
            "Old groceries",
            "40.00",
            [user(ME, "40.00", "40.00")],
            date="2025-01-10T12:00:00Z",
            updated_at="2025-01-10T12:00:00Z",
        ),
    ]
    w = World(exps)
    s, _ = w.sync(since=date(2026, 1, 1))
    assert s.created == 4
    s2, _ = w.sync(full=True)
    assert s2.created == 1  # full history import picks up the old one


def test_manual_category_edit_is_preserved_and_locked():
    exps = basic_expenses()
    w = World(exps)
    w.sync()
    page = w.expense_rows()[f"sw:{ME}:1"]
    w.notion.update_page(page["id"], {"Category": p_select("Entertainment")})
    exps[0] = {**exps[0], "updated_at": "2026-10-15T09:00:00Z", "description": "Dinner at Zuni!"}
    w.sync()
    props = w.row(1)
    assert r_select(props, "Category") == "Entertainment"
    assert props["Category Locked"]["checkbox"] is True


def test_dry_run_writes_nothing():
    w = World(basic_expenses())
    writes = w.notion.writes
    s, dash = w.sync(dry_run=True)
    assert s.created == 4 and s.dry_run
    assert w.notion.writes == writes and dash.month.spent == D("60.25")


def test_dashboard_rendered_once_per_content_change():
    w = World(basic_expenses())
    s, _ = w.sync()
    assert s.dashboard_refreshed
    dash_page = next(b["id"] for b in w.notion.blocks[w.notion.root] if b["type"] == "child_page")
    blocks = w.notion.blocks[dash_page]
    assert (
        blocks[0]["type"] == "callout"
        and "Generated by TUF" in blocks[0]["callout"]["rich_text"][0]["plain_text"]
    )
    assert any(b["type"] == "image" for b in blocks)
    count = len(blocks)
    s2, _ = w.sync()
    assert not s2.dashboard_refreshed
    assert len(w.notion.blocks[dash_page]) == count
    # user content above the marker is preserved across refreshes
    w.notion.blocks[dash_page].insert(
        0, {"id": "user-block", "type": "paragraph", "paragraph": {"rich_text": []}}
    )
    s3, _ = w.sync(force_dashboard=True)
    assert s3.dashboard_refreshed and w.notion.blocks[dash_page][0]["id"] == "user-block"
    assert len(w.notion.blocks[dash_page]) == count + 1


def test_budget_rows_and_category_aggregates():
    w = World(basic_expenses())
    cat = next(
        p for p in w.notion.rows(S.CATEGORIES) if r_text(p["properties"], "Name") == "Dining Out"
    )
    w.notion.update_page(cat["id"], {"Monthly Budget": {"number": 40}})
    w.sync()
    budgets = {
        r_text(p["properties"], "Budget Key"): p["properties"] for p in w.notion.rows(S.BUDGETS)
    }
    assert set(budgets) == {"2026-10|overall", "2026-10|cat|Dining Out"}
    dining = budgets["2026-10|cat|Dining Out"]
    assert r_number(dining, "Actual Spending") == D("35.25")
    assert r_number(dining, "Utilization") == D("0.881")
    assert r_select(dining, "Status") == "Amber"
    cat_props = w.notion.pages[cat["id"]]["properties"]
    assert r_number(cat_props, "Spent This Month") == D("35.25")


def test_ai_quota_then_retry_on_next_run():
    exps = [expense(1, "zuni w/ friends", "40.00", [user(ME, "40.00", "40.00")])]
    ai = FakeAI(exc=AIQuotaExceeded("429"))
    w = World(exps, ai=ai, GEMINI_API_KEY="AIza-test")
    _, dash = w.sync()
    assert r_select(w.row(1), "Classification") == "Pending"
    assert dash.pending_classification == 1
    ai.exc, ai.response = None, ai_ok()
    w.sync(full=True)
    assert r_select(w.row(1), "Classification") == "AI"
    assert r_select(w.row(1), "Category") == "Dining Out"
    calls = len(ai.calls)
    w.sync(full=True)
    assert len(ai.calls) == calls  # cached: unchanged expense never re-sent


def test_recurring_pattern_suggests_subcategory_and_approval_promotes():
    exps = [
        expense(
            i,
            f"DoorDash order {i}",
            "20.00",
            [user(ME, "20.00", "20.00")],
            updated_at=f"2026-10-0{i}T10:00:00Z",
        )
        for i in range(1, 4)
    ]
    w = World(exps)
    s, _ = w.sync()
    assert s.suggestions_created == 1
    sugg = next(
        p
        for p in w.notion.rows(S.CATEGORIES)
        if r_text(p["properties"], "Name") == "Takeout & Delivery"
    )
    assert r_select(sugg["properties"], "Status") == "Suggested"
    assert r_select(w.row(1), "Subcategory") is None
    w.notion.update_page(sugg["id"], {"Status": p_select("Active")})  # user approves
    s2, _ = w.sync()
    # rows re-fetched in the overlap window are re-gated as normal updates; older rows are
    # promoted by the promotion pass. Either way, all three get the approved subcategory.
    assert s2.promoted >= 1 and s2.promoted + s2.updated == 3
    for i in (1, 2, 3):
        assert r_select(w.row(i), "Subcategory") == "Takeout & Delivery"
        assert r_text(w.row(i), "Suggested Subcategory") == ""


def test_foreign_currency_converted_or_flagged():
    exps = [
        expense(
            1,
            "Tapas",
            "48.00",
            [user(ME, "48.00", "24.00"), user(ALEX, "0", "24.00")],
            currency="EUR",
        )
    ]

    class Rates:
        def rate(self, base, quote, on):
            return FxRate(base, quote, D("1.10"), on, "test")

    w = World(exps, rates=Rates())
    w.sync()
    assert r_number(w.row(1), "Spending") == D("26.4")
    assert r_number(w.row(1), "FX Rate") == D("1.1")
    assert r_number(w.row(1), "My Share") == D("24")
    w2 = World(exps)
    _, dash2 = w2.sync()
    assert r_select(w2.row(1), "Status") == "Needs FX"
    assert w2.row(1)["Spending"]["number"] is None
    assert dash2.month.spent == 0 and dash2.month.excluded_unconverted == {"EUR": 1}


def test_unset_workspace_gives_actionable_error():
    w = World(basic_expenses())
    w.notion.blocks[w.notion.root] = []
    with pytest.raises(WorkspaceError, match="tuf setup"):
        w.sync()


def test_schema_drift_missing_property_is_added():
    w = World(basic_expenses())
    ds = next(k for k, v in w.notion.data_sources.items() if v["title"] == S.EXPENSES)
    del w.notion.data_sources[ds]["properties"]["Merchant"]
    s, _ = w.sync()
    assert s.ok and "Merchant" in w.notion.data_sources[ds]["properties"]


def test_schema_wrong_type_blocks_sync():
    w = World(basic_expenses())
    ds = next(k for k, v in w.notion.data_sources.items() if v["title"] == S.EXPENSES)
    w.notion.data_sources[ds]["properties"]["Spending"]["type"] = "rich_text"
    with pytest.raises(WorkspaceError, match="Spending"):
        w.sync()
