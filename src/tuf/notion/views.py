"""Optional native Notion views on the dashboard page (``tuf setup --views``).

Uses the Views API (``POST /v1/views``, Notion-Version 2026-03-11). Notion's Free plan
allows only ONE chart per workspace, so TUF creates a single native chart (monthly spending,
which never needs a date filter) plus non-chart linked views. Every view is best-effort:
failures are reported and the manual steps in docs/NOTION_SETUP.md apply instead.
"""

from __future__ import annotations

from typing import Any

from tuf.http import ApiError
from tuf.notion.client import NotionClient
from tuf.notion.store import Workspace


def _prop_ids(client: NotionClient, ds: str) -> dict[str, str]:
    props = client.retrieve_data_source(ds).get("properties", {})
    return {name: str(p.get("id")) for name, p in props.items()}


def create_native_views(client: NotionClient, ws: Workspace) -> list[str]:
    if not ws.dashboard_page_id:
        return ["skipped: no dashboard page"]
    exp = _prop_ids(client, ws.expenses)
    parent = {"create_database": {"parent": {"type": "page_id", "page_id": ws.dashboard_page_id}}}
    specs: list[tuple[str, dict[str, Any]]] = [
        (
            "Monthly spending (native chart)",
            {
                "data_source_id": ws.expenses,
                "name": "Monthly spending",
                "type": "chart",
                "configuration": {
                    "type": "chart",
                    "chart_type": "column",
                    "x_axis": {
                        "type": "date",
                        "property_id": exp.get("Date"),
                        "group_by": "month",
                        "sort": {"type": "ascending"},
                    },
                    "y_axis": {"aggregator": "sum", "property_id": exp.get("Spending")},
                },
                **parent,
            },
        ),
        (
            "Recent expenses (gallery)",
            {
                "data_source_id": ws.expenses,
                "name": "Recent expenses",
                "type": "gallery",
                "filter": {"property": "Status", "select": {"equals": "Active"}},
                "sorts": [{"property": "Date", "direction": "descending"}],
                **parent,
            },
        ),
        (
            "Trips (gallery)",
            {
                "data_source_id": ws.trips,
                "name": "Trips",
                "type": "gallery",
                "sorts": [{"property": "Start Date", "direction": "descending"}],
                **parent,
            },
        ),
    ]
    results = []
    for label, body in specs:
        try:
            client.create_view(body)
            results.append(f"created {label}")
        except ApiError as exc:
            results.append(f"could not create {label} ({exc}); see docs/NOTION_SETUP.md")
    return results
