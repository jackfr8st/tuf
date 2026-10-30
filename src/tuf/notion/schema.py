"""Notion workspace template: data source schemas and validation.

TUF creates four databases under one root page the user shares with the integration:

* TUF Expenses    - one row per Splitwise expense (identity: "Sync Key")
* TUF Categories  - taxonomy, budgets, rules, suggestion review
* TUF Trips       - trips (analytical dimension), linked to expenses via a relation
* TUF Budgets     - one row per (month, scope) with maintained aggregates and gauges

Aggregates (spending totals, utilization, gauges) are *maintained by TUF* with exact
Decimal arithmetic rather than Notion rollups, because rollups cannot filter out deleted or
settlement rows and formula/rollup values may return ``unsupported`` on large relations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXPENSES, CATEGORIES, TRIPS, BUDGETS = "TUF Expenses", "TUF Categories", "TUF Trips", "TUF Budgets"
DASHBOARD = "TUF Dashboard"

ICONS = {EXPENSES: "💳", CATEGORIES: "🏷️", TRIPS: "🧳", BUDGETS: "🎯", DASHBOARD: "📊"}

_CURRENCY_FORMATS = {
    "USD": "dollar",
    "EUR": "euro",
    "GBP": "pound",
    "INR": "rupee",
    "JPY": "yen",
    "CAD": "canadian_dollar",
    "AUD": "australian_dollar",
    "SGD": "singapore_dollar",
    "CNY": "yuan",
    "KRW": "won",
    "BRL": "real",
    "MXN": "mexican_peso",
    "CHF": "franc",
    "HKD": "hong_kong_dollar",
    "NZD": "new_zealand_dollar",
    "SEK": "krona",
}

TYPE_OPTIONS = [
    ("Expense", "blue"),
    ("Refund", "green"),
    ("Paid for Others", "gray"),
    ("Reimbursement", "gray"),
    ("Settlement Paid", "brown"),
    ("Settlement Received", "brown"),
    ("No Share", "default"),
]
STATUS_OPTIONS = [
    ("Active", "green"),
    ("Deleted", "red"),
    ("Removed", "gray"),
    ("Needs FX", "yellow"),
]
SOURCE_OPTIONS = [
    ("Manual", "purple"),
    ("Rule", "blue"),
    ("Learned", "blue"),
    ("Keyword", "default"),
    ("Splitwise Category", "default"),
    ("AI", "pink"),
    ("Pending", "yellow"),
    ("Fallback", "gray"),
]
CATEGORY_COLORS = {
    "Dining Out": "orange",
    "Groceries": "green",
    "Snacks": "yellow",
    "Entertainment": "purple",
    "Shopping": "pink",
    "Transportation": "blue",
    "Stay": "brown",
    "Health & Insurance": "red",
    "Other": "gray",
}
CAT_STATUS_OPTIONS = [
    ("Active", "green"),
    ("Suggested", "yellow"),
    ("Rejected", "gray"),
    ("Archived", "default"),
]
BUDGET_STATUS_OPTIONS = [
    ("Green", "green"),
    ("Amber", "yellow"),
    ("Red", "red"),
    ("Over", "red"),
    ("No budget", "default"),
]


def money_format(currency: str) -> str:
    return _CURRENCY_FORMATS.get(currency.upper(), "number_with_commas")


def _select(options: list[tuple[str, str]]) -> dict[str, Any]:
    return {"select": {"options": [{"name": n, "color": c} for n, c in options]}}


def _num(fmt: str) -> dict[str, Any]:
    return {"number": {"format": fmt}}


_TEXT: dict[str, Any] = {"rich_text": {}}
_DATE: dict[str, Any] = {"date": {}}


def categories_schema(currency: str) -> dict[str, Any]:
    money = money_format(currency)
    return {
        "Name": {"title": {}},
        "Parent": _select([(n, c) for n, c in CATEGORY_COLORS.items()]),
        "Icon": _TEXT,
        "Monthly Budget": _num(money),
        "Classification Rules": _TEXT,
        "Status": _select(CAT_STATUS_OPTIONS),
        "Spent This Month": _num(money),
        "Budget Used": _num("percent"),
        "Gauge": _TEXT,
        "Occurrences": _num("number"),
    }


def trips_schema(currency: str) -> dict[str, Any]:
    money = money_format(currency)
    return {
        "Name": {"title": {}},
        "Start Date": _DATE,
        "End Date": _DATE,
        "Budget": _num(money),
        "Spending Total": _num(money),
        "Budget Used": _num("percent"),
        "Gauge": _TEXT,
        "Expense Count": _num("number"),
        "Top Category": _TEXT,
        "Splitwise Groups": _TEXT,
        "Tags": _TEXT,
    }


def budgets_schema(currency: str, categories_ds: str) -> dict[str, Any]:
    money = money_format(currency)
    return {
        "Name": {"title": {}},
        "Period": _TEXT,
        "Scope": _select([("Overall", "blue"), ("Category", "purple"), ("Subcategory", "pink")]),
        "Category": {
            "relation": {
                "data_source_id": categories_ds,
                "type": "single_property",
                "single_property": {},
            }
        },
        "Budget Limit": _num(money),
        "Actual Spending": _num(money),
        "Remaining": _num(money),
        "Utilization": _num("percent"),
        "Gauge": _TEXT,
        "Status": _select(BUDGET_STATUS_OPTIONS),
        "Budget Key": _TEXT,
    }


def expenses_schema(currency: str, trips_ds: str) -> dict[str, Any]:
    money = money_format(currency)
    return {
        "Name": {"title": {}},
        "Date": _DATE,
        "Spending": _num(money),
        "Category": _select([(n, c) for n, c in CATEGORY_COLORS.items()]),
        "Subcategory": {"select": {}},
        "Trip": {
            "relation": {"data_source_id": trips_ds, "type": "dual_property", "dual_property": {}}
        },
        "Merchant": _TEXT,
        "Group": {"select": {}},
        "Type": _select(TYPE_OPTIONS),
        "Status": _select(STATUS_OPTIONS),
        "My Share": _num("number_with_commas"),
        "Paid Share": _num("number_with_commas"),
        "Owed Share": _num("number_with_commas"),
        "Net": _num("number_with_commas"),
        "Total Cost": _num("number_with_commas"),
        "Currency": {"select": {}},
        "FX Rate": _num("number"),
        "FX Date": _DATE,
        "Month": _TEXT,
        "Classification": _select(SOURCE_OPTIONS),
        "Confidence": _num("percent"),
        "Category Locked": {"checkbox": {}},
        "Suggested Subcategory": _TEXT,
        "Description": _TEXT,
        "Sync Key": _TEXT,
        "Splitwise ID": _num("number"),
        "Account ID": _num("number"),
        "Class Key": _TEXT,
        "Source Updated": _DATE,
        "Last Synced": _DATE,
    }


@dataclass
class SchemaIssue:
    database: str
    prop: str
    problem: str  # "missing" | "wrong type: expected X, found Y"

    def __str__(self) -> str:
        return f"{self.database}: property '{self.prop}' {self.problem}"


def property_type(spec: dict[str, Any]) -> str:
    return next(iter(spec))


def validate_schema(
    database: str, expected: dict[str, Any], actual_props: dict[str, Any]
) -> list[SchemaIssue]:
    """Compare a live data source's properties against the template."""
    issues: list[SchemaIssue] = []
    actual_types = {name: p.get("type") for name, p in actual_props.items()}
    for name, spec in expected.items():
        want = property_type(spec)
        have = actual_types.get(name)
        if have is None:
            issues.append(SchemaIssue(database, name, "missing"))
        elif have != want:
            issues.append(SchemaIssue(database, name, f"wrong type: expected {want}, found {have}"))
    return issues
