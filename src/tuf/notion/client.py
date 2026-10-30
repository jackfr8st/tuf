"""Thin Notion REST client (API version 2026-03-11, data-source model).

Uses plain HTTPS via httpx so the behaviour is fully testable and independent of SDK
release cadence. Notion allows ~3 requests/second per integration; we pace at that rate
and honour ``Retry-After`` on 429.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tuf.http import HttpClient

NOTION_VERSION = "2026-03-11"
BASE_URL = "https://api.notion.com"


class NotionClient:
    def __init__(self, token: str, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(
            "Notion",
            BASE_URL,
            {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
            min_interval=0.34,
        )

    @property
    def request_count(self) -> int:
        return self._http.request_count

    def close(self) -> None:
        self._http.close()

    def _call(
        self, method: str, path: str, *, idempotent: bool | None = None, **kw: Any
    ) -> dict[str, Any]:
        data = self._http.request(method, path, idempotent=idempotent, **kw)
        return data if isinstance(data, dict) else {}

    # --- identity / pages ------------------------------------------------------------
    def me(self) -> dict[str, Any]:
        return self._call("GET", "/v1/users/me")

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/pages/{page_id}")

    def create_page(
        self,
        parent: dict[str, Any],
        properties: dict[str, Any],
        icon: str | None = None,
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"parent": parent, "properties": properties}
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        if children:
            body["children"] = children
        return self._call("POST", "/v1/pages", json=body)

    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any] | None = None,
        *,
        in_trash: bool | None = None,
        icon: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if properties:
            body["properties"] = properties
        if in_trash is not None:
            body["in_trash"] = in_trash
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        # Setting absolute property values is idempotent, so 5xx retries are safe.
        return self._call("PATCH", f"/v1/pages/{page_id}", json=body, idempotent=True)

    # --- databases / data sources ----------------------------------------------------
    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/databases/{database_id}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/data_sources/{data_source_id}")

    def create_database(
        self, parent_page_id: str, title: str, icon: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/databases",
            json={
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "title": [{"type": "text", "text": {"content": title}}],
                "icon": {"type": "emoji", "emoji": icon},
                "initial_data_source": {"properties": properties},
            },
        )

    def update_data_source(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "PATCH",
            f"/v1/data_sources/{data_source_id}",
            json={"properties": properties},
            idempotent=True,
        )

    def query_all(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 100,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate every page of a data source query (cursor pagination)."""
        cursor: str | None = None
        seen = 0
        while True:
            body: dict[str, Any] = {"page_size": page_size}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            # Queries are reads: safe to retry on 5xx.
            data = self._call(
                "POST", f"/v1/data_sources/{data_source_id}/query", json=body, idempotent=True
            )
            for page in data.get("results", []):
                yield page
                seen += 1
                if limit is not None and seen >= limit:
                    return
            if not data.get("has_more") or not data.get("next_cursor"):
                return
            cursor = data["next_cursor"]

    # --- blocks ------------------------------------------------------------------------
    def list_children(self, block_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._call("GET", f"/v1/blocks/{block_id}/children", params=params)
            out.extend(data.get("results", []))
            if not data.get("has_more") or not data.get("next_cursor"):
                return out
            cursor = data["next_cursor"]

    def append_children(
        self, block_id: str, children: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for i in range(0, len(children), 100):  # API limit: 100 blocks per request
            data = self._call(
                "PATCH",
                f"/v1/blocks/{block_id}/children",
                json={"children": children[i : i + 100]},
                idempotent=False,
            )
            results.extend(data.get("results", []))
        return results

    def update_block(self, block_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("PATCH", f"/v1/blocks/{block_id}", json=body, idempotent=True)

    def delete_block(self, block_id: str) -> None:
        self._call("DELETE", f"/v1/blocks/{block_id}")

    # --- files & views -----------------------------------------------------------------
    def upload_file(self, filename: str, content: bytes, content_type: str) -> str:
        """Direct upload (<= 5 MiB on Free workspaces). Returns the file_upload id.
        The upload must be attached to a block within one hour."""
        created = self._call(
            "POST",
            "/v1/file_uploads",
            json={"mode": "single_part", "filename": filename, "content_type": content_type},
        )
        upload_id = str(created["id"])
        self._call(
            "POST",
            f"/v1/file_uploads/{upload_id}/send",
            files={"file": (filename, content, content_type)},
        )
        return upload_id

    def create_view(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/v1/views", json=body)
