"""Notion persistence: workspace discovery/setup, schema migration, and row I/O.

Notion is TUF's only durable store. There is no local state file: the sync checkpoint is
derived from the newest "Source Updated" timestamp already in the Expenses database, so
independent GitHub Actions runs need nothing but credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from tuf.categorization.taxonomy import (
    PRIMARIES,
    SUBCATEGORIES,
    CategoryDef,
    CategoryStatus,
    Taxonomy,
)
from tuf.models import LedgerEntry
from tuf.notion import schema as S
from tuf.notion.client import NotionClient
from tuf.notion.mapping import (
    entry_properties,
    p_date,
    p_number,
    p_relation,
    p_select,
    p_text,
    p_title,
    page_to_entry,
    r_date,
    r_number,
    r_relation,
    r_select,
    r_text,
)
from tuf.trips.engine import TripDef

log = logging.getLogger(__name__)


class WorkspaceError(Exception):
    pass


@dataclass
class Workspace:
    root_page_id: str
    database_ids: dict[str, str] = field(default_factory=dict)  # title -> database id
    data_sources: dict[str, str] = field(default_factory=dict)  # title -> data source id
    dashboard_page_id: str | None = None

    @property
    def expenses(self) -> str:
        return self.data_sources[S.EXPENSES]

    @property
    def categories(self) -> str:
        return self.data_sources[S.CATEGORIES]

    @property
    def trips(self) -> str:
        return self.data_sources[S.TRIPS]

    @property
    def budgets(self) -> str:
        return self.data_sources[S.BUDGETS]

    def missing(self) -> list[str]:
        want = [S.EXPENSES, S.CATEGORIES, S.TRIPS, S.BUDGETS]
        out = [t for t in want if t not in self.data_sources]
        if not self.dashboard_page_id:
            out.append(S.DASHBOARD)
        return out


def _first_data_source(
    client: NotionClient, database_id: str, db: dict[str, Any] | None = None
) -> str:
    db = db or client.retrieve_database(database_id)
    sources = db.get("data_sources") or []
    if not sources:
        raise WorkspaceError(f"database {database_id} has no data source")
    return str(sources[0]["id"])


def discover(client: NotionClient, root_page_id: str) -> Workspace:
    """Find TUF databases and the dashboard among the root page's children by title."""
    ws = Workspace(root_page_id)
    for block in client.list_children(root_page_id):
        btype = block.get("type")
        if btype == "child_database":
            title = str(block.get("child_database", {}).get("title", "")).strip()
            if title in (S.EXPENSES, S.CATEGORIES, S.TRIPS, S.BUDGETS):
                ws.database_ids[title] = block["id"]
                ws.data_sources[title] = _first_data_source(client, block["id"])
        elif btype == "child_page":
            if str(block.get("child_page", {}).get("title", "")).strip() == S.DASHBOARD:
                ws.dashboard_page_id = block["id"]
    return ws


def setup_workspace(
    client: NotionClient, root_page_id: str, currency: str
) -> tuple[Workspace, list[str]]:
    """Create whatever is missing (idempotent). Returns the workspace and a list of actions."""
    ws = discover(client, root_page_id)
    actions: list[str] = []
    if not ws.dashboard_page_id:
        page = client.create_page(
            {"type": "page_id", "page_id": root_page_id},
            {"title": p_title(S.DASHBOARD)["title"]},
            icon=S.ICONS[S.DASHBOARD],
        )
        ws.dashboard_page_id = str(page["id"])
        actions.append(f"created page '{S.DASHBOARD}'")

    def create(title: str, props: dict[str, Any]) -> None:
        db = client.create_database(root_page_id, title, S.ICONS[title], props)
        ws.database_ids[title] = str(db["id"])
        ws.data_sources[title] = _first_data_source(
            client, str(db["id"]), db if db.get("data_sources") else None
        )
        actions.append(f"created database '{title}'")

    if S.CATEGORIES not in ws.data_sources:
        create(S.CATEGORIES, S.categories_schema(currency))
        seed_categories(client, ws.categories)
        actions.append("seeded default categories")
    if S.TRIPS not in ws.data_sources:
        create(S.TRIPS, S.trips_schema(currency))
    if S.BUDGETS not in ws.data_sources:
        create(S.BUDGETS, S.budgets_schema(currency, ws.categories))
    if S.EXPENSES not in ws.data_sources:
        create(S.EXPENSES, S.expenses_schema(currency, ws.trips))
        _rename_trip_backlink(client, ws)
    return ws, actions


def _rename_trip_backlink(client: NotionClient, ws: Workspace) -> None:
    """The dual relation creates an auto-named property on Trips; call it 'Expenses'."""
    try:
        props = client.retrieve_data_source(ws.trips).get("properties", {})
        for name, prop in props.items():
            if prop.get("type") == "relation" and name != "Expenses":
                client.update_data_source(ws.trips, {name: {"name": "Expenses"}})
                return
    except Exception as exc:  # cosmetic only; never fail setup on this
        log.info("could not rename Trips backlink property: %s", exc)


def seed_categories(client: NotionClient, categories_ds: str) -> None:
    parent = {"type": "data_source_id", "data_source_id": categories_ds}
    for p in PRIMARIES:
        client.create_page(
            parent,
            {
                "Name": p_title(p.name),
                "Icon": p_text(p.icon),
                "Status": p_select(CategoryStatus.ACTIVE),
            },
            icon=p.icon,
        )
    for s in SUBCATEGORIES:
        if s.seeded:
            client.create_page(
                parent,
                {
                    "Name": p_title(s.name),
                    "Parent": p_select(s.parent),
                    "Icon": p_text(s.icon),
                    "Status": p_select(CategoryStatus.ACTIVE),
                },
                icon=s.icon,
            )


def expected_schemas(ws: Workspace, currency: str) -> dict[str, dict[str, Any]]:
    return {
        S.CATEGORIES: S.categories_schema(currency),
        S.TRIPS: S.trips_schema(currency),
        S.BUDGETS: S.budgets_schema(currency, ws.data_sources.get(S.CATEGORIES, "")),
        S.EXPENSES: S.expenses_schema(currency, ws.data_sources.get(S.TRIPS, "")),
    }


def check_schema(
    client: NotionClient, ws: Workspace, currency: str, *, apply: bool
) -> list[S.SchemaIssue]:
    """Validate every data source. With ``apply``, add missing properties (never deletes or
    changes existing ones). Returns the issues that remain."""
    remaining: list[S.SchemaIssue] = []
    for title, expected in expected_schemas(ws, currency).items():
        if title not in ws.data_sources:
            continue
        actual = client.retrieve_data_source(ws.data_sources[title]).get("properties", {})
        issues = S.validate_schema(title, expected, actual)
        missing = {i.prop: expected[i.prop] for i in issues if i.problem == "missing"}
        if apply and missing and "Name" not in missing:
            client.update_data_source(ws.data_sources[title], missing)
            log.info("added %d missing properties to %s", len(missing), title)
            issues = [i for i in issues if i.problem != "missing"]
        remaining.extend(issues)
    return remaining


# ----------------------------------------------------------------------------- rows


def _split_list(text: str) -> list[str]:
    raw = text.replace("\n", ",").replace(";", ",")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _icon(page: dict[str, Any]) -> str:
    icon = page.get("icon") or {}
    return str(icon.get("emoji") or "") if isinstance(icon, dict) else ""


class NotionStore:
    def __init__(self, client: NotionClient, ws: Workspace) -> None:
        self.client = client
        self.ws = ws
        self.category_rows: dict[tuple[str | None, str], dict[str, Any]] = {}
        self.trip_rows: dict[str, dict[str, Any]] = {}

    # --- categories ----------------------------------------------------------------
    def load_taxonomy(self) -> Taxonomy:
        tax = Taxonomy()
        for page in self.client.query_all(self.ws.categories):
            props = page.get("properties", {})
            name = r_text(props, "Name").strip()
            if not name:
                continue
            status = r_select(props, "Status") or CategoryStatus.ACTIVE
            parent = r_select(props, "Parent")
            cat = CategoryDef(
                name=name,
                parent=parent,
                icon=r_text(props, "Icon") or _icon(page),
                monthly_budget=r_number(props, "Monthly Budget"),
                rules=tuple(
                    k.lstrip("#") for k in _split_list(r_text(props, "Classification Rules"))
                ),
                status=status,
                page_id=str(page["id"]),
            )
            tax.add(cat)
            self.category_rows[(parent, name)] = page
        if not tax.primaries:
            log.warning("Categories database is empty; using built-in defaults")
            return Taxonomy.defaults()
        return tax

    def create_category(
        self, name: str, parent: str | None, icon: str, status: str, occurrences: int
    ) -> str:
        page = self.client.create_page(
            {"type": "data_source_id", "data_source_id": self.ws.categories},
            {
                "Name": p_title(name),
                "Parent": p_select(parent),
                "Icon": p_text(icon),
                "Status": p_select(status),
                "Occurrences": p_number(occurrences),
            },
            icon=icon,
        )
        return str(page["id"])

    # --- trips ---------------------------------------------------------------------
    def load_trips(self) -> list[TripDef]:
        trips: list[TripDef] = []
        for page in self.client.query_all(self.ws.trips):
            props = page.get("properties", {})
            name = r_text(props, "Name").strip()
            if not name:
                continue
            t = TripDef(
                name=name,
                start=r_date(props, "Start Date"),
                end=r_date(props, "End Date"),
                budget=r_number(props, "Budget"),
                group_refs=set(_split_list(r_text(props, "Splitwise Groups"))),
                tags={x.lstrip("#") for x in _split_list(r_text(props, "Tags"))},
                page_id=str(page["id"]),
            )
            trips.append(t)
            self.trip_rows[t.key] = page
        return trips

    def create_trip(self, trip: TripDef, source: str) -> str:
        props: dict[str, Any] = {
            "Name": p_title(trip.name),
            "Splitwise Groups": p_text(", ".join(sorted(trip.group_refs))),
        }
        if source in ("hashtag", "bracket"):
            props["Tags"] = p_text(trip.name)
        page = self.client.create_page(
            {"type": "data_source_id", "data_source_id": self.ws.trips}, props, icon="🧳"
        )
        trip.page_id = str(page["id"])
        return trip.page_id

    # --- expenses ------------------------------------------------------------------
    def load_expenses(self, trips: list[TripDef]) -> list[LedgerEntry]:
        trip_key_by_page = {t.page_id: t.key for t in trips if t.page_id}
        out: list[LedgerEntry] = []
        for page in self.client.query_all(self.ws.expenses):
            entry = page_to_entry(page, trip_key_by_page)
            if entry is not None:
                out.append(entry)
        return out

    def create_expense(self, e: LedgerEntry, trip_page_id: str | None, now: datetime) -> str:
        page = self.client.create_page(
            {"type": "data_source_id", "data_source_id": self.ws.expenses},
            entry_properties(e, trip_page_id, now),
        )
        return str(page["id"])

    def update_expense(
        self, page_id: str, e: LedgerEntry, trip_page_id: str | None, now: datetime
    ) -> None:
        self.client.update_page(page_id, entry_properties(e, trip_page_id, now))

    def trash_page(self, page_id: str) -> None:
        self.client.update_page(page_id, in_trash=True)

    # --- aggregates ----------------------------------------------------------------
    def update_if_changed(self, page: dict[str, Any], props: dict[str, Any]) -> bool:
        """Write only properties whose stored value differs (saves rate-limited requests)."""
        current = page.get("properties", {})
        changed = {k: v for k, v in props.items() if not _same(current.get(k), v)}
        if not changed:
            return False
        self.client.update_page(str(page["id"]), changed)
        return True

    def load_budget_rows(self, period: str) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        flt = {"property": "Period", "rich_text": {"equals": period}}
        for page in self.client.query_all(self.ws.budgets, filter=flt):
            key = r_text(page.get("properties", {}), "Budget Key")
            if key:
                rows[key] = page
        return rows

    def create_budget_row(self, props: dict[str, Any]) -> None:
        self.client.create_page(
            {"type": "data_source_id", "data_source_id": self.ws.budgets}, props, icon="🎯"
        )


def _same(stored: Any, desired: dict[str, Any]) -> bool:
    """Compare a stored Notion property value with a value we are about to write."""
    if not isinstance(stored, dict):
        return False
    kind = next(iter(desired))
    want = desired[kind]
    have = stored.get(kind)
    if kind in ("title", "rich_text"):
        text = "".join(
            t.get("plain_text") or t.get("text", {}).get("content", "") for t in have or []
        )
        return text == "".join(t["text"]["content"] for t in want)
    if kind == "number":
        if have is None or want is None:
            return have is want
        return Decimal(repr(have)) == Decimal(repr(want))
    if kind == "select":
        have_name = have.get("name") if isinstance(have, dict) else None
        want_name = want.get("name") if isinstance(want, dict) else None
        return bool(have_name == want_name)
    if kind == "date":
        have_start = have.get("start") if isinstance(have, dict) else None
        want_start = want.get("start") if isinstance(want, dict) else None
        return bool((have_start or "")[:10] == (want_start or "")[:10])
    if kind == "relation":
        return sorted(r.get("id") for r in have or []) == sorted(r["id"] for r in want)
    return bool(have == want)


__all__ = [
    "NotionStore",
    "Workspace",
    "WorkspaceError",
    "check_schema",
    "discover",
    "p_date",
    "p_relation",
    "r_relation",
    "setup_workspace",
]
