"""Core domain models. These are independent of Splitwise and Notion SDK/JSON shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

Date = date  # alias: LedgerEntry has a field named `date`


class TxnType(StrEnum):
    """What an expense means *for the authenticated user*."""

    EXPENSE = "Expense"  # user consumed a positive share
    REFUND = "Refund"  # user's share is negative (money back on consumption)
    PAID_FOR_OTHERS = "Paid for Others"  # user advanced money but consumed nothing
    REIMBURSEMENT = "Reimbursement"  # user received money on others' behalf, consumed nothing
    SETTLEMENT_PAID = "Settlement Paid"  # Splitwise payment: user paid someone back
    SETTLEMENT_RECEIVED = "Settlement Received"  # Splitwise payment: user was paid back
    NO_SHARE = "No Share"  # user is listed but has no paid/owed amount

    @property
    def counts_as_spending(self) -> bool:
        return self in (TxnType.EXPENSE, TxnType.REFUND)

    @property
    def is_settlement(self) -> bool:
        return self in (TxnType.SETTLEMENT_PAID, TxnType.SETTLEMENT_RECEIVED)


class SyncStatus(StrEnum):
    ACTIVE = "Active"
    DELETED = "Deleted"  # deleted in Splitwise (soft-deleted in Notion, reversible)
    REMOVED = "Removed"  # no longer visible to the user in Splitwise (full reconcile)
    NEEDS_FX = "Needs FX"  # foreign currency with no available conversion rate


class ClassSource(StrEnum):
    MANUAL = "Manual"  # user locked the category in Notion
    RULE = "Rule"  # user-defined rule from the Categories database
    LEARNED = "Learned"  # copied from a user-confirmed identical expense
    KEYWORD = "Keyword"  # built-in deterministic keyword rules
    SPLITWISE = "Splitwise Category"  # mapped from the category chosen in Splitwise
    AI = "AI"  # Gemini classification (validated)
    PENDING = "Pending"  # AI wanted but unavailable; retried on a later run
    FALLBACK = "Fallback"  # nothing matched; "Other"


@dataclass(frozen=True)
class Share:
    user_id: int
    paid_share: Decimal
    owed_share: Decimal
    net_balance: Decimal | None


@dataclass(frozen=True)
class SplitwiseExpense:
    """A parsed Splitwise expense. Only fields TUF needs; no participant names or emails."""

    id: int
    description: str
    cost: Decimal
    currency: str
    date: datetime
    updated_at: datetime
    deleted_at: datetime | None
    group_id: int | None
    payment: bool
    shares: tuple[Share, ...]
    splitwise_category: str | None = None

    def share_for(self, user_id: int) -> Share | None:
        for s in self.shares:
            if s.user_id == user_id:
                return s
        return None


@dataclass(frozen=True)
class FinancialFacts:
    """Deterministic accounting result for one expense from the user's point of view."""

    txn_type: TxnType
    paid_share: Decimal
    owed_share: Decimal
    net_contribution: Decimal  # paid - owed; >0 means others owe the user
    personal_spending: Decimal  # consumption in the original currency (0 for settlements)
    deleted: bool
    warnings: tuple[str, ...] = ()


@dataclass
class Classification:
    primary: str
    subcategory: str | None
    merchant: str | None
    clean_title: str
    source: ClassSource
    confidence: Decimal
    suggested_subcategory: str | None = None
    trip_hint: str | None = None


@dataclass
class LedgerEntry:
    """One synchronized expense row (the Notion Expenses record, in domain form)."""

    sync_key: str
    expense_id: int
    account_id: int
    title: str
    description: str
    date: date
    currency: str
    total_cost: Decimal
    paid_share: Decimal
    owed_share: Decimal
    my_share: Decimal
    net_contribution: Decimal
    txn_type: TxnType
    status: SyncStatus
    spending: Decimal | None  # in reporting currency; None => unconvertible
    fx_rate: Decimal | None
    fx_date: Date | None
    primary: str
    subcategory: str | None
    suggested_subcategory: str | None
    merchant: str | None
    group_name: str
    group_id: int | None
    trip_key: str | None
    class_source: ClassSource
    confidence: Decimal
    locked: bool
    class_key: str
    source_updated_at: datetime
    page_id: str | None = None
    trip_page_id: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def counted(self) -> bool:
        """Whether this entry contributes to spending totals in the reporting currency."""
        return (
            self.status == SyncStatus.ACTIVE
            and self.txn_type.counts_as_spending
            and self.spending is not None
        )


def sync_key(account_id: int, expense_id: int) -> str:
    """Stable identity key: one Notion row per (Splitwise account, expense)."""
    return f"sw:{account_id}:{expense_id}"
