"""Splitwise API v3.0 connector (https://dev.splitwise.com).

Authentication uses a personal API key (or OAuth2 access token) as a Bearer token.
Expenses are fetched across all groups and non-group expenses by calling
``get_expenses`` without ``group_id``/``friend_id``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tuf.http import ApiError, HttpClient
from tuf.models import SplitwiseExpense
from tuf.splitwise.parse import SplitwiseParseError, parse_datetime, parse_expense

log = logging.getLogger(__name__)

BASE_URL = "https://secure.splitwise.com/api/v3.0"
MAX_PAGES = 5000  # hard safety bound against a misbehaving API (500k expenses at 100/page)


@dataclass(frozen=True)
class SplitwiseUser:
    id: int
    default_currency: str | None


@dataclass(frozen=True)
class GroupInfo:
    id: int
    name: str
    group_type: str | None


@dataclass
class FetchResult:
    expenses: list[SplitwiseExpense]
    parse_errors: list[str]
    unparsed_ids: set[int]  # expenses that exist but failed validation (never "removed")


class SplitwiseClient:
    def __init__(self, api_key: str, http: HttpClient | None = None, page_size: int = 100) -> None:
        self._http = http or HttpClient(
            "Splitwise", BASE_URL, {"Authorization": f"Bearer {api_key}"}, min_interval=0.25
        )
        self.page_size = page_size

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self._http.request("GET", path, params=params)
        if not isinstance(data, dict):
            raise ApiError("Splitwise", None, f"unexpected response for {path}")
        errors = data.get("errors") or data.get("error")
        if errors:
            raise ApiError("Splitwise", None, f"{path}: {errors}")
        return data

    def current_user(self) -> SplitwiseUser:
        user = self._get("/get_current_user").get("user") or {}
        if "id" not in user:
            raise ApiError("Splitwise", None, "get_current_user returned no user id")
        return SplitwiseUser(id=int(user["id"]), default_currency=user.get("default_currency"))

    def groups(self) -> dict[int, GroupInfo]:
        out: dict[int, GroupInfo] = {}
        for g in self._get("/get_groups").get("groups") or []:
            gid = int(g.get("id") or 0)
            if gid == 0:
                continue  # group 0 is Splitwise's pseudo-group for non-group expenses
            out[gid] = GroupInfo(gid, str(g.get("name") or f"Group {gid}"), g.get("group_type"))
        return out

    def fetch_expenses(
        self,
        *,
        updated_after: datetime | None = None,
        dated_after: datetime | None = None,
        upper_bound: datetime | None = None,
    ) -> FetchResult:
        """Fetch every matching expense, paging until the API returns an empty page.

        We never stop on a "short" page because the server may cap ``limit`` below what we
        asked for. ``upper_bound`` (``updated_before``) pins the result set so that edits made
        while paging do not shift offsets; such edits are picked up on the next run via the
        overlap window.
        """
        params: dict[str, Any] = {"limit": self.page_size}
        if updated_after:
            params["updated_after"] = updated_after.isoformat()
        if dated_after:
            params["dated_after"] = dated_after.isoformat()
        if upper_bound:
            params["updated_before"] = upper_bound.isoformat()

        by_id: dict[int, SplitwiseExpense] = {}
        parse_errors: list[str] = []
        unparsed: set[int] = set()
        for raw in self._iter_raw(params):
            try:
                expense = parse_expense(raw)
            except SplitwiseParseError as exc:
                parse_errors.append(str(exc))
                if str(raw.get("id", "")).isdigit():
                    unparsed.add(int(raw["id"]))
                continue
            prev = by_id.get(expense.id)
            if prev is None or expense.updated_at >= prev.updated_at:
                by_id[expense.id] = expense
        return FetchResult(list(by_id.values()), parse_errors, unparsed)

    def _iter_raw(self, base_params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        offset = 0
        for _page in range(MAX_PAGES):
            data = self._get("/get_expenses", {**base_params, "offset": offset})
            batch = data.get("expenses") or []
            if not batch:
                return
            yield from batch
            offset += len(batch)
        raise ApiError("Splitwise", None, f"pagination exceeded {MAX_PAGES} pages; aborting")


def max_updated_at(expenses: list[SplitwiseExpense]) -> datetime | None:
    return max((e.updated_at for e in expenses), default=None)


__all__ = [
    "FetchResult",
    "GroupInfo",
    "SplitwiseClient",
    "SplitwiseUser",
    "max_updated_at",
    "parse_datetime",
]
