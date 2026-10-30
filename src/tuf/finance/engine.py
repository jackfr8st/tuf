"""Deterministic personal-share accounting.

Rules (see docs/ARCHITECTURE.md, "Financial invariants"):

* ``personal_spending = owed_share`` for ordinary expenses, never ``paid + owed``.
* ``net_contribution = paid_share - owed_share``.
* Splitwise payments (``payment: true``) are settlements: they move cash but are never
  consumption, so ``personal_spending = 0``. In a payment from A to B, Splitwise records
  A with ``paid_share = amount`` and B with ``owed_share = amount``.
* If the user has no entry in ``users`` they are not involved: return ``None`` instead of
  fabricating a share.
* Deleted expenses keep their raw shares for audit but contribute ``0`` spending.
"""

from __future__ import annotations

from decimal import Decimal

from tuf.models import FinancialFacts, SplitwiseExpense, TxnType
from tuf.money import ZERO, minor_unit


def compute_facts(expense: SplitwiseExpense, user_id: int) -> FinancialFacts | None:
    share = expense.share_for(user_id)
    if share is None:
        return None

    paid, owed = share.paid_share, share.owed_share
    net = paid - owed

    if expense.payment:
        spending = ZERO
        if net > 0:
            txn = TxnType.SETTLEMENT_PAID
        elif net < 0:
            txn = TxnType.SETTLEMENT_RECEIVED
        else:
            txn = TxnType.NO_SHARE
    elif owed > 0:
        txn, spending = TxnType.EXPENSE, owed
    elif owed < 0:
        txn, spending = TxnType.REFUND, owed
    elif paid > 0:
        txn, spending = TxnType.PAID_FOR_OTHERS, ZERO
    elif paid < 0:
        txn, spending = TxnType.REIMBURSEMENT, ZERO
    else:
        txn, spending = TxnType.NO_SHARE, ZERO

    deleted = expense.deleted_at is not None
    return FinancialFacts(
        txn_type=txn,
        paid_share=paid,
        owed_share=owed,
        net_contribution=net,
        personal_spending=ZERO if deleted else spending,
        deleted=deleted,
        warnings=tuple(invariant_warnings(expense, net, share.net_balance)),
    )


def invariant_warnings(
    expense: SplitwiseExpense, net: Decimal, reported_net: Decimal | None
) -> list[str]:
    """Data-quality checks. They never change amounts; they surface inconsistent source data."""
    out: list[str] = []
    if reported_net is not None and reported_net != net:
        out.append(f"net_balance {reported_net} != paid-owed {net}")
    if expense.shares:
        tolerance = minor_unit(expense.currency)
        total_paid = sum((s.paid_share for s in expense.shares), ZERO)
        total_owed = sum((s.owed_share for s in expense.shares), ZERO)
        if abs(total_paid - expense.cost) > tolerance:
            out.append(f"sum(paid_share) {total_paid} != cost {expense.cost}")
        if abs(total_owed - expense.cost) > tolerance:
            out.append(f"sum(owed_share) {total_owed} != cost {expense.cost}")
    return out
