"""Convert between ``LedgerEntry`` and Notion page properties."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tuf.models import ClassSource, LedgerEntry, SyncStatus, TxnType
from tuf.money import ZERO, MoneyError, parse_amount, to_json_number

MAX_TEXT = 2000

# ----------------------------------------------------------------------------- writers


def _rt(text: str) -> list[dict[str, Any]]:
    text = (text or "")[:MAX_TEXT]
    return [{"type": "text", "text": {"content": text}}] if text else []


def p_title(text: str) -> dict[str, Any]:
    return {"title": _rt(text or "Untitled")}


def p_text(text: str | None) -> dict[str, Any]:
    return {"rich_text": _rt(text or "")}


def p_number(value: Decimal | int | None) -> dict[str, Any]:
    if isinstance(value, int):
        return {"number": value}
    return {"number": to_json_number(value)}


def safe_option(name: str | None) -> str | None:
    """Select option names cannot contain commas and are limited to 100 characters."""
    if not name:
        return None
    cleaned = " ".join(name.replace(",", " ").split())[:100]
    return cleaned or None


def p_select(name: str | None) -> dict[str, Any]:
    name = safe_option(name)
    return {"select": {"name": name} if name else None}


def p_date(value: date | datetime | None) -> dict[str, Any]:
    if value is None:
        return {"date": None}
    return {"date": {"start": value.isoformat()}}


def p_checkbox(value: bool) -> dict[str, Any]:
    return {"checkbox": bool(value)}


def p_relation(page_ids: list[str]) -> dict[str, Any]:
    return {"relation": [{"id": pid} for pid in page_ids]}


# ----------------------------------------------------------------------------- readers


def _prop(props: dict[str, Any], name: str) -> dict[str, Any]:
    value = props.get(name)
    return value if isinstance(value, dict) else {}


def r_text(props: dict[str, Any], name: str) -> str:
    p = _prop(props, name)
    parts = p.get("title") if p.get("type") == "title" else p.get("rich_text")
    if parts is None:
        parts = p.get("title") or p.get("rich_text") or []
    return "".join(
        str(t.get("plain_text") or t.get("text", {}).get("content", "")) for t in parts or []
    )


def r_number(props: dict[str, Any], name: str) -> Decimal | None:
    value = _prop(props, name).get("number")
    if value is None:
        return None
    try:
        return parse_amount(value)
    except MoneyError:
        return None


def r_select(props: dict[str, Any], name: str) -> str | None:
    sel = _prop(props, name).get("select")
    return str(sel["name"]) if isinstance(sel, dict) and sel.get("name") else None


def r_checkbox(props: dict[str, Any], name: str) -> bool:
    return bool(_prop(props, name).get("checkbox"))


def r_relation(props: dict[str, Any], name: str) -> list[str]:
    rel = _prop(props, name).get("relation") or []
    return [str(r["id"]) for r in rel if isinstance(r, dict) and r.get("id")]


def r_date(props: dict[str, Any], name: str) -> date | None:
    d = _prop(props, name).get("date")
    if not isinstance(d, dict) or not d.get("start"):
        return None
    return date.fromisoformat(str(d["start"])[:10])


def r_datetime(props: dict[str, Any], name: str) -> datetime | None:
    d = _prop(props, name).get("date")
    if not isinstance(d, dict) or not d.get("start"):
        return None
    raw = str(d["start"]).replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw) if "T" in raw else datetime.fromisoformat(raw + "T00:00:00")
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- expenses


def output_hash(primary: str | None, subcategory: str | None) -> str:
    """Hash of the category TUF wrote. A mismatch on read means the user edited it."""
    raw = f"{primary or ''}|{subcategory or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]


def entry_properties(
    e: LedgerEntry, trip_page_id: str | None, synced_at: datetime
) -> dict[str, Any]:
    return {
        "Name": p_title(e.title),
        "Date": p_date(e.date),
        "Spending": p_number(e.spending),
        "Category": p_select(e.primary),
        "Subcategory": p_select(e.subcategory),
        "Trip": p_relation([trip_page_id] if trip_page_id else []),
        "Merchant": p_text(e.merchant),
        "Group": p_select(e.group_name),
        "Type": p_select(e.txn_type.value),
        "Status": p_select(e.status.value),
        "My Share": p_number(e.my_share),
        "Paid Share": p_number(e.paid_share),
        "Owed Share": p_number(e.owed_share),
        "Net": p_number(e.net_contribution),
        "Total Cost": p_number(e.total_cost),
        "Currency": p_select(e.currency),
        "FX Rate": p_number(e.fx_rate),
        "FX Date": p_date(e.fx_date),
        "Month": p_text(f"{e.date.year:04d}-{e.date.month:02d}"),
        "Classification": p_select(e.class_source.value),
        "Confidence": p_number(e.confidence),
        "Category Locked": p_checkbox(e.locked),
        "Suggested Subcategory": p_text(e.suggested_subcategory),
        "Description": p_text(e.description),
        "Sync Key": p_text(e.sync_key),
        "Splitwise ID": p_number(e.expense_id),
        "Account ID": p_number(e.account_id),
        "Class Key": p_text(f"{e.class_key}.{output_hash(e.primary, e.subcategory)}"),
        "Source Updated": p_date(e.source_updated_at),
        "Last Synced": p_date(synced_at),
    }


def page_to_entry(page: dict[str, Any], trip_key_by_page: dict[str, str]) -> LedgerEntry | None:
    """Parse a Notion Expenses row. Returns None for rows TUF did not create."""
    props = page.get("properties") or {}
    key = r_text(props, "Sync Key")
    if not key.startswith("sw:"):
        return None
    try:
        txn = TxnType(r_select(props, "Type") or "Expense")
    except ValueError:
        txn = TxnType.EXPENSE
    try:
        status = SyncStatus(r_select(props, "Status") or "Active")
    except ValueError:
        status = SyncStatus.ACTIVE
    try:
        source = ClassSource(r_select(props, "Classification") or "Fallback")
    except ValueError:
        source = ClassSource.MANUAL
    locked_box = r_checkbox(props, "Category Locked")
    primary = r_select(props, "Category") or "Other"
    subcategory = r_select(props, "Subcategory")
    class_key, _, written = r_text(props, "Class Key").partition(".")
    edited = bool(written) and written != output_hash(primary, subcategory)
    locked = locked_box or edited
    trips = r_relation(props, "Trip")
    updated = r_datetime(props, "Source Updated") or datetime(1970, 1, 1, tzinfo=UTC)
    _, account, expense = [*key.split(":"), "0", "0"][:3]
    return LedgerEntry(
        sync_key=key,
        expense_id=int(expense) if expense.isdigit() else 0,
        account_id=int(account) if account.isdigit() else 0,
        title=r_text(props, "Name"),
        description=r_text(props, "Description"),
        date=r_date(props, "Date") or updated.date(),
        currency=r_select(props, "Currency") or "",
        total_cost=r_number(props, "Total Cost") or ZERO,
        paid_share=r_number(props, "Paid Share") or ZERO,
        owed_share=r_number(props, "Owed Share") or ZERO,
        my_share=r_number(props, "My Share") or ZERO,
        net_contribution=r_number(props, "Net") or ZERO,
        txn_type=txn,
        status=status,
        spending=r_number(props, "Spending"),
        fx_rate=r_number(props, "FX Rate"),
        fx_date=r_date(props, "FX Date"),
        primary=primary,
        subcategory=subcategory,
        suggested_subcategory=r_text(props, "Suggested Subcategory") or None,
        merchant=r_text(props, "Merchant") or None,
        group_name=r_select(props, "Group") or "",
        group_id=None,
        trip_key=trip_key_by_page.get(trips[0]) if trips else None,
        class_source=ClassSource.MANUAL if locked else source,
        confidence=r_number(props, "Confidence") or ZERO,
        locked=locked,
        class_key=class_key,
        source_updated_at=updated,
        page_id=str(page.get("id")),
        trip_page_id=trips[0] if trips else None,
        extra={"lock_mismatch": locked != locked_box, "created_time": page.get("created_time")},
    )


def comparable(e: LedgerEntry) -> tuple[object, ...]:
    """Fields TUF writes. If equal for desired vs stored, the row is left untouched."""
    return (
        e.title,
        e.date,
        e.spending,
        e.primary,
        e.subcategory,
        e.trip_key,
        e.merchant or None,
        safe_option(e.group_name),
        e.txn_type,
        e.status,
        e.my_share,
        e.paid_share,
        e.owed_share,
        e.net_contribution,
        e.total_cost,
        e.currency,
        e.fx_rate,
        e.fx_date,
        e.class_source,
        e.confidence,
        e.locked,
        e.suggested_subcategory or None,
        e.description,
        e.class_key,
        e.source_updated_at.replace(second=0, microsecond=0),  # tolerate minute precision
    )
