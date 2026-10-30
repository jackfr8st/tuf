"""Sync orchestrator: Splitwise -> finance -> categories -> trips -> Notion -> analytics.

Guarantees
----------
* Idempotent: one Notion row per ``sw:<account>:<expense>`` key; unchanged rows are not
  rewritten; duplicate rows (e.g. from overlapping runs) are detected and trashed.
* Stateless between runs: the incremental checkpoint is the newest "Source Updated" value
  already in Notion, minus an overlap window. Missed or delayed schedules just widen the
  next window.
* Safe partial failure: rows are written in ascending ``updated_at`` order and a transient
  error stops further writes, so the derived checkpoint never skips past an unwritten row.
* Deletions are soft (Status=Deleted/Removed, Spending=0), reversible, and never trashed.
* Full reconciliation refuses implausibly large removals unless forced.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as dtime
from decimal import Decimal
from typing import Any

from tuf.analytics import charts as chart_mod
from tuf.analytics.engine import AnalyticsInput, Dashboard, Thresholds, build_dashboard, gauge
from tuf.categorization.ai import AIClassifier
from tuf.categorization.engine import Categorizer, ClassifyInput, learned_from
from tuf.categorization.suggestions import find_suggestions
from tuf.categorization.taxonomy import CategoryDef, CategoryStatus, Taxonomy
from tuf.categorization.text import classification_key
from tuf.config import Config
from tuf.finance.engine import compute_facts
from tuf.finance.fx import NoRates, RateProvider, convert
from tuf.http import ApiError
from tuf.models import (
    Classification,
    ClassSource,
    FinancialFacts,
    LedgerEntry,
    SplitwiseExpense,
    SyncStatus,
    sync_key,
)
from tuf.money import ZERO
from tuf.notion import dashboard as dash_mod
from tuf.notion.client import NotionClient
from tuf.notion.mapping import comparable, p_date, p_number, p_relation, p_select, p_text, p_title
from tuf.notion.store import NotionStore, WorkspaceError, check_schema, discover
from tuf.splitwise.client import GroupInfo, SplitwiseClient
from tuf.trips.engine import DEFAULT_GROUP_PATTERN, TripDef, TripMatcher

log = logging.getLogger(__name__)


@dataclass
class SyncOptions:
    full: bool = False
    dry_run: bool = False
    since: date | None = None
    no_ai: bool = False
    reclassify: bool = False
    force_dashboard: bool = False
    force_reconcile: bool = False


@dataclass
class SyncSummary:
    mode: str = "incremental"
    dry_run: bool = False
    window_start: str | None = None
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    removed: int = 0
    restored: int = 0
    promoted: int = 0
    not_involved: int = 0
    duplicates_trashed: int = 0
    trips_created: int = 0
    suggestions_created: int = 0
    aggregates_updated: int = 0
    dashboard_refreshed: bool = False
    parse_errors: list[str] = field(default_factory=list)
    write_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    aborted: bool = False
    ai: dict[str, Any] = field(default_factory=dict)
    notion_requests: int = 0
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.write_errors and not self.aborted

    def lines(self) -> list[str]:
        verb = "would " if self.dry_run else ""
        out = [
            f"mode={self.mode}{' (dry run)' if self.dry_run else ''}"
            + (f" window_start={self.window_start}" if self.window_start else ""),
            f"fetched={self.fetched} {verb}create={self.created} {verb}update={self.updated} "
            f"unchanged={self.unchanged} deleted={self.deleted} removed={self.removed} "
            f"restored={self.restored} promoted={self.promoted}",
            f"trips_created={self.trips_created} suggestions={self.suggestions_created} "
            f"duplicates_trashed={self.duplicates_trashed} not_involved={self.not_involved} "
            f"aggregates_updated={self.aggregates_updated} "
            f"dashboard_refreshed={self.dashboard_refreshed}",
            f"ai={self.ai} notion_requests={self.notion_requests} seconds={self.seconds:.1f}",
        ]
        out += [f"WARNING: {w}" for w in self.warnings]
        out += [f"PARSE ERROR: {e}" for e in self.parse_errors]
        out += [f"WRITE ERROR: {e}" for e in self.write_errors]
        if self.aborted:
            out.append("ABORTED: stopped after a transient error; the next run resumes safely.")
        return out


def _transient(exc: ApiError) -> bool:
    return exc.status is None or exc.status >= 500 or exc.status == 429 or exc.retryable


class SyncEngine:
    def __init__(
        self,
        cfg: Config,
        splitwise: SplitwiseClient,
        notion: NotionClient,
        *,
        rates: RateProvider | None = None,
        ai: AIClassifier | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.cfg = cfg
        self.sw = splitwise
        self.notion = notion
        self.rates = rates or NoRates()
        self.ai = ai
        self.clock = clock

    # ================================================================== run
    def run(self, opts: SyncOptions) -> tuple[SyncSummary, Dashboard | None]:
        t0 = time.monotonic()
        cfg = self.cfg
        s = SyncSummary(dry_run=opts.dry_run)
        now = self.clock()
        today = now.astimezone(cfg.tz).date()

        user = self.sw.current_user()
        groups = self.sw.groups()
        ws = discover(self.notion, cfg.notion_root_page_id)
        if ws.missing():
            raise WorkspaceError(
                "Notion workspace is not set up (missing: "
                f"{', '.join(ws.missing())}). Run `tuf setup` first."
            )
        issues = check_schema(self.notion, ws, cfg.reporting_currency, apply=not opts.dry_run)
        blocking = [str(i) for i in issues if i.problem != "missing" or not opts.dry_run]
        if blocking:
            raise WorkspaceError("Notion schema is incompatible:\n  " + "\n  ".join(blocking))
        store = NotionStore(self.notion, ws)
        taxonomy = store.load_taxonomy()
        trip_defs = store.load_trips()
        existing = store.load_expenses(trip_defs)

        index, duplicates = _index(existing)
        mine = [e for e in index.values() if e.account_id == user.id]

        # ------------------------------------------------------------- fetch
        full = opts.full or opts.reclassify or opts.since is not None or not mine
        updated_after = dated_after = None
        if full:
            s.mode = "reclassify" if opts.reclassify else "full"
            start = opts.since or cfg.backfill_start
            if start:
                dated_after = datetime.combine(start, dtime.min, tzinfo=cfg.tz)
                s.window_start = start.isoformat()
        else:
            checkpoint = max(e.source_updated_at for e in mine)
            updated_after = checkpoint - timedelta(hours=cfg.sync_overlap_hours)
            s.window_start = updated_after.isoformat()
        fetched = self.sw.fetch_expenses(
            updated_after=updated_after, dated_after=dated_after, upper_bound=now
        )
        s.fetched = len(fetched.expenses)
        s.parse_errors = fetched.parse_errors

        # ------------------------------------------------------------- classify
        matcher = TripMatcher(
            trip_defs,
            group_pattern=cfg.trip_group_pattern or DEFAULT_GROUP_PATTERN,
            auto_create_from_tags=cfg.trip_auto_create,
        )
        ai = None if (opts.no_ai or not cfg.ai_enabled) else self.ai
        categorizer = Categorizer(
            taxonomy,
            learned_from(index.values()),
            ai,
            ai_batch_size=cfg.ai_batch_size,
            ai_max_requests=cfg.ai_max_requests,
            min_confidence=cfg.ai_min_confidence,
            trip_names=matcher.names(),
            use_cache=not opts.reclassify,
        )
        offline = Categorizer(taxonomy, categorizer.learned)

        work: list[tuple[SplitwiseExpense, Any, str, LedgerEntry | None]] = []
        inputs: list[ClassifyInput] = []
        removals: list[LedgerEntry] = []
        for exp in sorted(fetched.expenses, key=lambda e: e.updated_at):
            facts = compute_facts(exp, user.id)
            key = sync_key(user.id, exp.id)
            prev = index.get(key)
            if facts is None:
                s.not_involved += 1
                if prev is not None and prev.status != SyncStatus.REMOVED:
                    removals.append(prev)
                continue
            for w in facts.warnings:
                s.warnings.append(f"expense {exp.id}: {w}")
            work.append((exp, facts, key, prev))
            if not facts.deleted:
                inputs.append(
                    ClassifyInput(
                        key,
                        exp.description,
                        exp.splitwise_category,
                        _context(exp, groups, matcher),
                        prev,
                    )
                )
        classified = categorizer.classify_all(inputs)
        s.ai = {
            "enabled": ai is not None,
            "requests": categorizer.stats.requests,
            "items": categorizer.stats.items_sent,
            "failures": categorizer.stats.failures,
            "quota_exhausted": categorizer.stats.quota_exhausted,
        }

        # ------------------------------------------------------------- build rows
        trip_pages: dict[str, str | None] = {t.key: t.page_id for t in trip_defs}
        changes: list[tuple[LedgerEntry, LedgerEntry | None]] = []
        final: dict[str, LedgerEntry] = dict(index)
        for exp, facts, key, prev in work:
            cls = classified.get(key) or _keep_or_offline(prev, exp, offline)
            group_name = group_name_for(exp.group_id, groups)
            trip = matcher.match(
                exp.description, exp.group_id, group_name if exp.group_id else None, cls.trip_hint
            )
            trip_key = trip.key if trip else (prev.trip_key if prev else None)
            if trip and trip.is_new and trip.key not in trip_pages:
                trip_pages[trip.key] = None
                s.trips_created += 1
                if not opts.dry_run:
                    trip_pages[trip.key] = store.create_trip(matcher.trips[trip.key], trip.source)

            desired = make_entry(
                exp,
                facts,
                cls,
                user.id,
                group_name,
                trip_key,
                prev,
                cfg.reporting_currency,
                cfg.tz,
                self.rates,
            )
            if (
                prev is None
                or comparable(desired) != comparable(prev)
                or prev.extra.get("lock_mismatch")
            ):
                changes.append((desired, prev))
            else:
                s.unchanged += 1
            final[key] = desired

        # ------------------------------------------------------------- reconcile (full)
        if full and not opts.reclassify:
            seen = {e.id for e in fetched.expenses} | fetched.unparsed_ids
            stale = [
                e
                for e in mine
                if e.expense_id not in seen
                and e.status in (SyncStatus.ACTIVE, SyncStatus.NEEDS_FX)
                and (dated_after is None or e.date >= dated_after.date())
            ]
            limit = max(5, int(Decimal(len(mine)) * cfg.reconcile_max_removal_ratio))
            if len(stale) > limit and not opts.force_reconcile:
                s.warnings.append(
                    f"reconciliation would remove {len(stale)} of {len(mine)} rows (limit "
                    f"{limit}); skipped as a safety measure. Re-run with --force-reconcile "
                    "if this is expected."
                )
            else:
                removals.extend(stale)
        for prev in removals:
            desired = replace(prev, status=SyncStatus.REMOVED, spending=ZERO)
            changes.append((desired, prev))
            final[prev.sync_key] = desired

        # ------------------------------------------------------------- write expenses
        changes.sort(key=lambda c: c[0].source_updated_at)
        self._write_changes(changes, store, trip_pages, now, s, opts.dry_run)
        for dup in duplicates:
            s.duplicates_trashed += 1
            if not opts.dry_run and dup.page_id:
                try:
                    store.trash_page(dup.page_id)
                except ApiError as exc:
                    s.write_errors.append(f"trash duplicate {dup.sync_key}: {exc}")

        # ------------------------------------------------------------- dynamic subcategories
        if not s.aborted:
            self._suggestions(final, taxonomy, store, s, opts.dry_run)
            self._promote(final, taxonomy, store, trip_pages, now, s, opts.dry_run)

        # ------------------------------------------------------------- analytics
        all_trips = list(matcher.trips.values())
        for t in all_trips:
            t.page_id = t.page_id or trip_pages.get(t.key)
        dashboard = build_dashboard(
            AnalyticsInput(
                entries=list(final.values()),
                today=today,
                currency=cfg.reporting_currency,
                overall_budget=cfg.monthly_budget,
                category_budgets={
                    n: c.monthly_budget
                    for n, c in taxonomy.primaries.items()
                    if c.monthly_budget and c.usable
                },
                subcategory_budgets={
                    k: c.monthly_budget
                    for k, c in taxonomy.subcategories.items()
                    if c.monthly_budget and c.usable
                },
                trips=all_trips,
                thresholds=Thresholds(cfg.budget_amber_pct, cfg.budget_red_pct),
                group_names={g.id: g.name for g in groups.values()},
            )
        )
        if not opts.dry_run and not s.aborted:
            self._write_aggregates(dashboard, taxonomy, store, s)
        if ws.dashboard_page_id and not s.aborted:
            svgs = chart_mod.render_all(dashboard) if cfg.dashboard_charts else {}
            names = {t.key: t.name for t in all_trips}
            try:
                s.dashboard_refreshed = dash_mod.refresh(
                    self.notion,
                    ws.dashboard_page_id,
                    dashboard,
                    taxonomy,
                    svgs,
                    names,
                    now.astimezone(cfg.tz),
                    force=opts.force_dashboard,
                    dry_run=opts.dry_run,
                )
            except ApiError as exc:
                s.write_errors.append(f"dashboard: {exc}")

        s.notion_requests = self.notion.request_count
        s.seconds = time.monotonic() - t0
        return s, dashboard

    # ================================================================== helpers
    def _write_changes(
        self,
        changes: list[tuple[LedgerEntry, LedgerEntry | None]],
        store: NotionStore,
        trip_pages: dict[str, str | None],
        now: datetime,
        s: SyncSummary,
        dry_run: bool,
    ) -> None:
        for desired, prev in changes:
            if prev is None:
                s.created += 1
            else:
                s.updated += 1
                if desired.status == SyncStatus.DELETED and prev.status != SyncStatus.DELETED:
                    s.deleted += 1
                elif desired.status == SyncStatus.REMOVED and prev.status != SyncStatus.REMOVED:
                    s.removed += 1
                elif prev.status in (SyncStatus.DELETED, SyncStatus.REMOVED) and desired.status in (
                    SyncStatus.ACTIVE,
                    SyncStatus.NEEDS_FX,
                ):
                    s.restored += 1
            if dry_run:
                continue
            trip_page = trip_pages.get(desired.trip_key) if desired.trip_key else None
            try:
                if prev is None or not prev.page_id:
                    desired.page_id = store.create_expense(desired, trip_page, now)
                else:
                    store.update_expense(prev.page_id, desired, trip_page, now)
            except ApiError as exc:
                if prev is None:
                    s.created -= 1
                else:
                    s.updated -= 1
                s.write_errors.append(f"{desired.sync_key}: {exc}")
                if _transient(exc):
                    s.aborted = True
                    return

    def _suggestions(
        self,
        final: dict[str, LedgerEntry],
        taxonomy: Taxonomy,
        store: NotionStore,
        s: SyncSummary,
        dry_run: bool,
    ) -> None:
        status = (
            CategoryStatus.ACTIVE if self.cfg.subcategory_auto_approve else CategoryStatus.SUGGESTED
        )
        for sug in find_suggestions(final.values(), taxonomy, self.cfg.subcategory_min_occurrences):
            s.suggestions_created += 1
            page_id = None
            if not dry_run:
                try:
                    page_id = store.create_category(
                        sug.name, sug.parent, sug.icon, status, sug.occurrences
                    )
                except ApiError as exc:
                    s.write_errors.append(f"category suggestion {sug.name}: {exc}")
                    continue
            taxonomy.add(
                CategoryDef(sug.name, sug.parent, sug.icon, status=status, page_id=page_id)
            )

    def _promote(
        self,
        final: dict[str, LedgerEntry],
        taxonomy: Taxonomy,
        store: NotionStore,
        trip_pages: dict[str, str | None],
        now: datetime,
        s: SyncSummary,
        dry_run: bool,
    ) -> None:
        """Assign subcategories that became Active (approved by the user or auto-approved)
        to earlier expenses that were waiting with a suggestion."""
        promotions: list[tuple[LedgerEntry, LedgerEntry | None]] = []
        for key, e in final.items():
            if e.locked or not e.suggested_subcategory or e.status != SyncStatus.ACTIVE:
                continue
            active = taxonomy.active_subcategory(e.primary, e.suggested_subcategory)
            if active:
                promotions.append((replace(e, subcategory=active, suggested_subcategory=None), e))
                final[key] = promotions[-1][0]
        before = s.updated
        self._write_changes(promotions, store, trip_pages, now, s, dry_run)
        s.promoted = s.updated - before
        s.updated = before

    def _write_aggregates(
        self, d: Dashboard, taxonomy: Taxonomy, store: NotionStore, s: SyncSummary
    ) -> None:
        """Maintain Categories / Trips / Budgets aggregate columns (only changed values)."""
        try:
            self._write_category_stats(d, store, s)
            self._write_trip_stats(d, store, s)
            self._write_budgets(d, taxonomy, store, s)
        except ApiError as exc:
            s.write_errors.append(f"aggregates: {exc}")

    def _write_category_stats(self, d: Dashboard, store: NotionStore, s: SyncSummary) -> None:
        spent = {c.name: c for c in d.categories}
        sub_spent = {(c.name, n): v for c in d.categories for n, v in c.subcategories}
        for (parent, name), page in store.category_rows.items():
            if parent:
                amount = sub_spent.get((parent, name), ZERO)
                line = next(
                    (b for b in d.budgets if b.parent == parent and b.category == name), None
                )
            else:
                ct = spent.get(name)
                amount = ct.amount if ct else ZERO
                line = ct.budget if ct else None
            props = {
                "Spent This Month": p_number(amount),
                "Budget Used": p_number(_fraction(line.utilization if line else None)),
                "Gauge": p_text(line.gauge if line and line.limit else ""),
            }
            if store.update_if_changed(page, props):
                s.aggregates_updated += 1

    def _write_trip_stats(self, d: Dashboard, store: NotionStore, s: SyncSummary) -> None:
        for t in d.trips:
            page = store.trip_rows.get(t.key)
            if page is None and t.page_id:
                page = {"id": t.page_id, "properties": {}}
            if page is None:
                continue
            props: dict[str, Any] = {
                "Spending Total": p_number(t.total),
                "Budget Used": p_number(_fraction(t.utilization)),
                "Gauge": p_text(gauge(t.utilization) if t.budget else ""),
                "Expense Count": p_number(t.count),
                "Top Category": p_text(t.by_category[0][0] if t.by_category else ""),
            }
            stored = page.get("properties", {})
            if t.start and not (stored.get("Start Date") or {}).get("date"):
                props["Start Date"] = p_date(t.start)
            if t.end and not (stored.get("End Date") or {}).get("date"):
                props["End Date"] = p_date(t.end)
            if store.update_if_changed(page, props):
                s.aggregates_updated += 1

    def _write_budgets(
        self, d: Dashboard, taxonomy: Taxonomy, store: NotionStore, s: SyncSummary
    ) -> None:
        period = d.month.month
        rows = store.load_budget_rows(period)
        for line in d.budgets:
            if line.limit is None:
                continue
            if line.category is None:
                key, scope, cat_page = f"{period}|overall", "Overall", None
            elif line.parent:
                key, scope = f"{period}|sub|{line.parent}|{line.category}", "Subcategory"
                sub = taxonomy.subcategories.get((line.parent, line.category))
                cat_page = sub.page_id if sub else None
            else:
                key, scope = f"{period}|cat|{line.category}", "Category"
                prim = taxonomy.primaries.get(line.category)
                cat_page = prim.page_id if prim else None
            props: dict[str, Any] = {
                "Name": p_title(f"{period} · {line.name}"),
                "Period": p_text(period),
                "Scope": p_select(scope),
                "Budget Limit": p_number(line.limit),
                "Actual Spending": p_number(line.actual),
                "Remaining": p_number(line.remaining),
                "Utilization": p_number(_fraction(line.utilization)),
                "Gauge": p_text(line.gauge),
                "Status": p_select(line.status),
                "Budget Key": p_text(key),
                "Category": p_relation([cat_page] if cat_page else []),
            }
            if key in rows:
                if store.update_if_changed(rows[key], props):
                    s.aggregates_updated += 1
            else:
                store.create_budget_row(props)
                s.aggregates_updated += 1


def make_entry(
    exp: SplitwiseExpense,
    facts: FinancialFacts,
    cls: Classification,
    user_id: int,
    group_name: str,
    trip_key: str | None,
    prev: LedgerEntry | None,
    currency: str,
    tz: tzinfo,
    rates: RateProvider,
) -> LedgerEntry:
    """Pure construction of the ledger row for one expense (shared by sync and demo)."""
    local_day = exp.date.astimezone(tz).date()
    status = SyncStatus.DELETED if facts.deleted else SyncStatus.ACTIVE
    spending: Decimal | None = ZERO
    fx = None
    if facts.txn_type.counts_as_spending and not facts.deleted:
        spending, fx = convert(facts.personal_spending, exp.currency, currency, local_day, rates)
        if spending is None:
            status = SyncStatus.NEEDS_FX
    locked = bool(prev and prev.locked)
    return LedgerEntry(
        sync_key=sync_key(user_id, exp.id),
        expense_id=exp.id,
        account_id=user_id,
        title=(prev.title if locked and prev else cls.clean_title)[:2000],
        description=exp.description[:2000],
        date=local_day,
        currency=exp.currency,
        total_cost=exp.cost,
        paid_share=facts.paid_share,
        owed_share=facts.owed_share,
        my_share=facts.personal_spending,
        net_contribution=facts.net_contribution,
        txn_type=facts.txn_type,
        status=status,
        spending=spending,
        fx_rate=fx.rate if fx else None,
        fx_date=fx.date if fx else None,
        primary=cls.primary,
        subcategory=cls.subcategory,
        suggested_subcategory=cls.suggested_subcategory,
        merchant=cls.merchant,
        group_name=group_name,
        group_id=exp.group_id,
        trip_key=trip_key,
        class_source=cls.source,
        confidence=cls.confidence,
        locked=locked,
        class_key=classification_key(exp.description, exp.splitwise_category),
        source_updated_at=exp.updated_at,
        page_id=prev.page_id if prev else None,
    )


def _fraction(util: Decimal | None) -> Decimal | None:
    return (util / 100).quantize(Decimal("0.001")) if util is not None else None


def _index(entries: list[LedgerEntry]) -> tuple[dict[str, LedgerEntry], list[LedgerEntry]]:
    """One row per key; extra rows (from overlapping runs) are returned as duplicates."""
    by_key: dict[str, list[LedgerEntry]] = {}
    for e in entries:
        by_key.setdefault(e.sync_key, []).append(e)
    index, dups = {}, []
    for key, rows in by_key.items():
        rows.sort(
            key=lambda e: (not e.locked, str(e.extra.get("created_time") or ""), e.page_id or "")
        )
        index[key] = rows[0]
        dups.extend(rows[1:])
    return index, dups


def group_name_for(group_id: int | None, groups: dict[int, GroupInfo]) -> str:
    if not group_id:
        return "Non-group"
    g = groups.get(group_id)
    return g.name if g else f"Group {group_id}"


def _context(
    exp: SplitwiseExpense, groups: dict[int, GroupInfo], matcher: TripMatcher
) -> str | None:
    """Anonymized hint for the AI: never the group's name or members."""
    if not exp.group_id:
        return None
    g = groups.get(exp.group_id)
    if g and (g.group_type == "trip" or matcher.is_trip_group(exp.group_id, g.name)):
        return "trip group"
    if g and g.group_type in ("apartment", "house"):
        return "household group"
    return "shared group"


def _keep_or_offline(
    prev: LedgerEntry | None, exp: SplitwiseExpense, offline: Categorizer
) -> Classification:
    """Deleted expenses are never sent to AI: keep the old labels or use rules only."""
    if prev is not None:
        return Classification(
            prev.primary,
            prev.subcategory,
            prev.merchant,
            prev.title,
            prev.class_source,
            prev.confidence,
            prev.suggested_subcategory,
        )
    result = offline.classify_without_ai(
        ClassifyInput("x", exp.description, exp.splitwise_category)
    )
    assert result is not None  # offline categorizer always returns a result
    return result


__all__ = ["ClassSource", "SyncEngine", "SyncOptions", "SyncSummary", "TripDef"]
