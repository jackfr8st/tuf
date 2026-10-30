"""Financial correctness: the authoritative personal-share rules."""

from datetime import date
from decimal import Decimal

import pytest

from factories import ALEX, JO, ME, SAM, expense, user
from tuf.finance.engine import compute_facts
from tuf.finance.fx import FxRate, convert
from tuf.models import TxnType
from tuf.money import MoneyError, fmt, parse_amount, pct, quantize, to_json_number
from tuf.splitwise.parse import SplitwiseParseError, parse_expense

D = Decimal


def facts(raw):
    return compute_facts(parse_expense(raw), ME)


def test_user_pays_entire_shared_bill():
    # $120 dinner for four, I pay all of it: I spent $30 and advanced $90.
    raw = expense(
        1,
        "Dinner",
        "120.00",
        [
            user(ME, "120.00", "30.00"),
            user(ALEX, "0", "30.00"),
            user(SAM, "0", "30.00"),
            user(JO, "0", "30.00"),
        ],
    )
    f = facts(raw)
    assert f.txn_type is TxnType.EXPENSE
    assert f.personal_spending == D("30.00")
    assert f.net_contribution == D("90.00")
    assert f.paid_share == D("120.00")
    assert f.warnings == ()


def test_another_person_pays():
    # Friend pays $100 hotel, my share $25: I spent $25 and owe $25.
    raw = expense(
        2,
        "Hotel",
        "100.00",
        [
            user(ALEX, "100.00", "25.00"),
            user(ME, "0", "25.00"),
            user(SAM, "0", "25.00"),
            user(JO, "0", "25.00"),
        ],
    )
    f = facts(raw)
    assert f.personal_spending == D("25.00")
    assert f.net_contribution == D("-25.00")
    assert f.paid_share == 0


def test_unequal_split():
    raw = expense(
        3,
        "Groceries",
        "87.43",
        [
            user(ME, "87.43", "52.10"),
            user(ALEX, "0", "35.33"),
        ],
    )
    f = facts(raw)
    assert f.personal_spending == D("52.10")
    assert f.net_contribution == D("35.33")


def test_personal_only_expense():
    f = facts(expense(4, "Netflix", "15.49", [user(ME, "15.49", "15.49")]))
    assert f.txn_type is TxnType.EXPENSE
    assert f.personal_spending == D("15.49")
    assert f.net_contribution == 0


def test_zero_owed_share_is_paid_for_others_not_spending():
    raw = expense(5, "Gift for Alex", "40.00", [user(ME, "40.00", "0"), user(ALEX, "0", "40.00")])
    f = facts(raw)
    assert f.txn_type is TxnType.PAID_FOR_OTHERS
    assert f.personal_spending == 0
    assert f.net_contribution == D("40.00")


def test_zero_paid_share():
    raw = expense(6, "Uber", "18.00", [user(ALEX, "18.00", "9.00"), user(ME, "0", "9.00")])
    f = facts(raw)
    assert f.personal_spending == D("9.00")
    assert f.paid_share == 0


def test_missing_user_allocation_returns_none_never_fabricates():
    raw = expense(
        7, "Their dinner", "50.00", [user(ALEX, "50.00", "25.00"), user(SAM, "0", "25.00")]
    )
    assert facts(raw) is None


def test_listed_with_zero_everything_is_no_share():
    raw = expense(8, "Pizza", "20.00", [user(ME, "0", "0"), user(ALEX, "20.00", "20.00")])
    f = facts(raw)
    assert f.txn_type is TxnType.NO_SHARE
    assert f.personal_spending == 0


def test_multiple_participants_with_cents():
    raw = expense(
        9,
        "Brunch",
        "100.00",
        [
            user(ME, "100.00", "33.34"),
            user(ALEX, "0", "33.33"),
            user(SAM, "0", "33.33"),
        ],
    )
    f = facts(raw)
    assert f.personal_spending == D("33.34")
    assert f.net_contribution == D("66.66")
    assert f.warnings == ()


def test_settlement_paid_is_not_spending():
    raw = expense(
        10, "Payment", "45.00", [user(ME, "45.00", "0"), user(ALEX, "0", "45.00")], payment=True
    )
    f = facts(raw)
    assert f.txn_type is TxnType.SETTLEMENT_PAID
    assert f.personal_spending == 0
    assert f.net_contribution == D("45.00")


def test_settlement_received_is_not_spending_even_though_owed_share_positive():
    raw = expense(
        11, "Payment", "45.00", [user(ALEX, "45.00", "0"), user(ME, "0", "45.00")], payment=True
    )
    f = facts(raw)
    assert f.txn_type is TxnType.SETTLEMENT_RECEIVED
    assert f.personal_spending == 0
    assert f.owed_share == D("45.00")


def test_refund_negative_share_reduces_spending():
    raw = expense(
        12, "Refund: returned shoes", "-60.00", [user(ME, "0", "-60.00"), user(ALEX, "-60.00", "0")]
    )
    f = facts(raw)
    assert f.txn_type is TxnType.REFUND
    assert f.personal_spending == D("-60.00")


def test_reimbursement_negative_paid_without_owed():
    raw = expense(13, "Adjustment", "-10.00", [user(ME, "-10.00", "0"), user(ALEX, "0", "-10.00")])
    f = facts(raw)
    assert f.txn_type is TxnType.REIMBURSEMENT
    assert f.personal_spending == 0


def test_foreign_currency_preserved_and_converted_with_recorded_rate():
    f = facts(
        expense(
            14,
            "Tapas",
            "48.00",
            [user(ME, "48.00", "24.00"), user(ALEX, "0", "24.00")],
            currency="EUR",
        )
    )
    assert f.personal_spending == D("24.00")

    class Rates:
        def rate(self, base, quote, on):
            return FxRate(base, quote, D("1.0857"), date(2026, 10, 9), "test")

    converted, fx = convert(f.personal_spending, "EUR", "USD", date(2026, 10, 10), Rates())
    assert converted == D("26.06")  # 24 * 1.0857 = 26.0568 -> 26.06
    assert fx is not None and fx.date == date(2026, 10, 9)


def test_no_rate_leaves_amount_unconverted():
    class NoRate:
        def rate(self, base, quote, on):
            return None

    assert convert(D("10"), "EUR", "USD", date(2026, 1, 1), NoRate()) == (None, None)
    assert convert(D("10"), "usd", "USD", date(2026, 1, 1), NoRate()) == (D("10"), None)
    assert convert(D("0"), "EUR", "USD", date(2026, 1, 1), NoRate()) == (D("0"), None)


def test_decimal_precision_no_float_drift():
    shares = [user(ME, "0.30", "0.10"), user(ALEX, "0", "0.10"), user(SAM, "0", "0.10")]
    f = facts(expense(15, "Gum", "0.30", shares))
    assert f.personal_spending == D("0.10")
    total = sum((D("0.10") for _ in range(3)), D("0"))
    assert total == D("0.30")  # would be 0.30000000000000004 with floats
    assert parse_amount(0.1) == D("0.1")
    assert parse_amount("19.99") + parse_amount("0.01") == D("20.00")


def test_edited_transaction_recomputes_from_latest_data():
    v1 = facts(
        expense(16, "Dinner", "60.00", [user(ME, "60.00", "30.00"), user(ALEX, "0", "30.00")])
    )
    v2 = facts(
        expense(
            16,
            "Dinner",
            "90.00",
            [user(ME, "90.00", "30.00"), user(ALEX, "0", "30.00"), user(SAM, "0", "30.00")],
            updated_at="2026-10-11T08:00:00Z",
        )
    )
    assert v1.personal_spending == v2.personal_spending == D("30.00")
    assert v2.net_contribution == D("60.00")


def test_deleted_transaction_contributes_zero_but_keeps_audit_values():
    f = facts(
        expense(
            17,
            "Dinner",
            "60.00",
            [user(ME, "60.00", "30.00"), user(ALEX, "0", "30.00")],
            deleted_at="2026-10-12T00:00:00Z",
        )
    )
    assert f.deleted
    assert f.personal_spending == 0
    assert f.owed_share == D("30.00")


def test_inconsistent_source_data_is_flagged_not_altered():
    f = facts(expense(18, "Odd", "50.00", [user(ME, "50.00", "20.00"), user(ALEX, "0", "20.00")]))
    assert f.personal_spending == D("20.00")
    assert any("owed_share" in w for w in f.warnings)


def test_parse_rejects_bad_data():
    with pytest.raises(SplitwiseParseError):
        parse_expense(
            {
                "id": 1,
                "cost": "x",
                "currency_code": "USD",
                "date": "2026-01-01",
                "updated_at": "2026-01-01",
                "users": [],
            }
        )
    with pytest.raises(SplitwiseParseError):
        parse_expense(
            {
                "id": 2,
                "cost": "1",
                "currency_code": "US",
                "date": "2026-01-01",
                "updated_at": "2026-01-01",
            }
        )
    with pytest.raises(MoneyError):
        parse_amount("NaN")


def test_money_helpers():
    assert quantize(D("2.345"), "USD") == D("2.34")  # banker's rounding
    assert quantize(D("1234.5"), "JPY") == D("1234")
    assert fmt(D("-1234.5"), "USD") == "-$1,234.50"
    assert fmt(D("1500"), "JPY") == "¥1,500"
    assert pct(D("85"), D("100")) == D("85.0")
    assert pct(D("1"), D("0")) is None
    assert to_json_number(D("12.34")) == 12.34
    assert to_json_number(D("12.00")) == 12
