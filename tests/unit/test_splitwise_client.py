"""Splitwise connector: identity, pagination, retries, error handling (mocked HTTP)."""

import httpx
import pytest

from factories import ALEX, ME, expense, user
from tuf.http import ApiError, HttpClient, RetryPolicy
from tuf.splitwise.client import BASE_URL, SplitwiseClient


def make_client(handler, page_size=2):
    http = HttpClient(
        "Splitwise",
        BASE_URL,
        {"Authorization": "Bearer test"},
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        policy=RetryPolicy(max_attempts=3),
    )
    return SplitwiseClient("test", http=http, page_size=page_size)


def test_current_user_uses_id_not_name():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer test"
        return httpx.Response(
            200, json={"user": {"id": ME, "first_name": "Sam", "default_currency": "USD"}}
        )

    u = make_client(handler).current_user()
    assert u.id == ME


def test_pagination_continues_past_short_pages_until_empty():
    # Server caps pages at 2 even when we ask for 3: a naive "short page = done" loop
    # would stop early.
    all_expenses = [expense(i, f"E{i}", "10.00", [user(ME, "10.00", "10.00")]) for i in range(1, 6)]
    seen_offsets = []

    def handler(req):
        offset = int(req.url.params["offset"])
        seen_offsets.append(offset)
        return httpx.Response(200, json={"expenses": all_expenses[offset : offset + 2]})

    result = make_client(handler, page_size=3).fetch_expenses()
    assert sorted(e.id for e in result.expenses) == [1, 2, 3, 4, 5]
    assert seen_offsets == [0, 2, 4, 5]


def test_duplicates_across_pages_keep_latest_version():
    old = expense(
        1, "Old", "10.00", [user(ME, "10.00", "10.00")], updated_at="2026-10-01T00:00:00Z"
    )
    new = expense(
        1, "New", "12.00", [user(ME, "12.00", "12.00")], updated_at="2026-10-02T00:00:00Z"
    )
    pages = [[new], [old], []]

    def handler(req):
        return httpx.Response(200, json={"expenses": pages.pop(0)})

    result = make_client(handler, page_size=1).fetch_expenses()
    assert [e.description for e in result.expenses] == ["New"]


def test_incremental_params_and_parse_errors_isolated():
    good = expense(1, "Good", "10.00", [user(ME, "10.00", "5.00"), user(ALEX, "0", "5.00")])
    bad = {
        "id": 2,
        "cost": "oops",
        "currency_code": "USD",
        "date": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
        "users": [],
    }
    captured = {}

    def handler(req):
        captured.update(req.url.params)
        if int(req.url.params["offset"]) > 0:
            return httpx.Response(200, json={"expenses": []})
        return httpx.Response(200, json={"expenses": [good, bad]})

    from datetime import UTC, datetime

    result = make_client(handler).fetch_expenses(
        updated_after=datetime(2026, 10, 1, tzinfo=UTC),
        upper_bound=datetime(2026, 10, 5, tzinfo=UTC),
    )
    assert [e.id for e in result.expenses] == [1]
    assert result.unparsed_ids == {2}
    assert captured["updated_after"].startswith("2026-10-01")
    assert captured["updated_before"].startswith("2026-10-05")


def test_retries_on_429_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"user": {"id": ME}})

    assert make_client(handler).current_user().id == ME
    assert len(calls) == 3


def test_retry_is_bounded():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(ApiError) as exc:
        make_client(handler).current_user()
    assert exc.value.status == 503
    assert len(calls) == 3


def test_auth_failure_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": "Invalid API key or OAuth access token"})

    with pytest.raises(ApiError) as exc:
        make_client(handler).current_user()
    assert exc.value.status == 401
    assert len(calls) == 1


def test_errors_in_200_body_raise():
    def handler(req):
        return httpx.Response(200, json={"errors": {"base": ["boom"]}})

    with pytest.raises(ApiError):
        make_client(handler).groups()


def test_post_not_retried_on_500_but_retried_on_429():
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(500)

    http = HttpClient(
        "X",
        "https://example.invalid",
        {},
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    with pytest.raises(ApiError):
        http.request("POST", "/thing", json={})
    assert calls == ["POST"]


def test_groups_skip_pseudo_group_zero():
    def handler(req):
        return httpx.Response(
            200,
            json={
                "groups": [
                    {"id": 0, "name": "Non-group expenses"},
                    {"id": 55, "name": "Trip: Big Sur 2026", "group_type": "trip"},
                ]
            },
        )

    groups = make_client(handler).groups()
    assert list(groups) == [55]
    assert groups[55].name == "Trip: Big Sur 2026"
