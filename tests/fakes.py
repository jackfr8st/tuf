"""In-memory fakes for Notion (client-level) and Splitwise (HTTP-level)."""

from __future__ import annotations

import copy
import itertools
from datetime import datetime
from typing import Any

import httpx

from tuf.http import ApiError, HttpClient, RetryPolicy
from tuf.splitwise.client import BASE_URL, SplitwiseClient

_ids = itertools.count(1)


def new_id() -> str:
    n = next(_ids)
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def _stored(kind: str, value: Any) -> dict[str, Any]:
    if kind in ("title", "rich_text"):
        value = [{**part, "plain_text": part["text"]["content"]} for part in value]
    return {"type": kind, kind: copy.deepcopy(value)}


class FakeNotion:
    """Implements the subset of NotionClient that TUF uses, with failure injection."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.blocks: dict[str, list[dict[str, Any]]] = {}
        self.data_sources: dict[str, dict[str, Any]] = {}
        self.databases: dict[str, str] = {}
        self.uploads: list[str] = []
        self.views: list[dict[str, Any]] = []
        self.request_count = 0
        self.fail: Any = None  # callable(op, **info) -> ApiError | None
        self.writes = 0
        self.root = new_id()
        self.blocks[self.root] = []

    def _tick(self, op: str, **info: Any) -> None:
        self.request_count += 1
        if self.fail:
            err = self.fail(op, **info)
            if err:
                raise err

    # pages ---------------------------------------------------------------------
    def create_page(self, parent, properties, icon=None, children=None):
        self._tick("create_page", parent=parent, properties=properties)
        self.writes += 1
        pid = new_id()
        props = {
            k: _stored("title", v)
            if isinstance(v, list)
            else _stored(next(iter(v)), next(iter(v.values())))
            for k, v in properties.items()
        }
        page = {
            "id": pid,
            "object": "page",
            "properties": props,
            "in_trash": False,
            "created_time": f"2026-01-01T00:00:{len(self.pages):02d}.000Z",
            "icon": {"type": "emoji", "emoji": icon} if icon else None,
            "parent": parent,
        }
        self.pages[pid] = page
        if parent.get("type") == "page_id":
            title = "".join(p["text"]["content"] for p in properties["title"])
            self.blocks.setdefault(parent["page_id"], []).append(
                {"id": pid, "type": "child_page", "child_page": {"title": title}}
            )
            self.blocks[pid] = []
        return {"id": pid}

    def update_page(self, page_id, properties=None, *, in_trash=None, icon=None):
        self._tick("update_page", page_id=page_id, properties=properties)
        self.writes += 1
        page = self.pages[page_id]
        for k, v in (properties or {}).items():
            page["properties"][k] = _stored(next(iter(v)), next(iter(v.values())))
        if in_trash is not None:
            page["in_trash"] = in_trash
        return page

    def retrieve_page(self, page_id):
        self._tick("retrieve_page")
        return self.pages[page_id]

    def me(self):
        self._tick("me")
        return {"object": "user", "type": "bot", "name": "TUF"}

    # databases -------------------------------------------------------------------
    def create_database(self, parent_page_id, title, icon, properties):
        self._tick("create_database", title=title)
        db, ds = new_id(), new_id()
        self.databases[db] = ds
        self.data_sources[ds] = {
            "title": title,
            "properties": {
                k: {"id": new_id()[:4], "type": next(iter(v))} for k, v in properties.items()
            },
        }
        self.blocks[parent_page_id].append(
            {"id": db, "type": "child_database", "child_database": {"title": title}}
        )
        return {"id": db, "data_sources": [{"id": ds, "name": title}]}

    def retrieve_database(self, database_id):
        self._tick("retrieve_database")
        return {"id": database_id, "data_sources": [{"id": self.databases[database_id]}]}

    def retrieve_data_source(self, data_source_id):
        self._tick("retrieve_data_source")
        return copy.deepcopy(self.data_sources[data_source_id])

    def update_data_source(self, data_source_id, properties):
        self._tick("update_data_source")
        props = self.data_sources[data_source_id]["properties"]
        for name, spec in properties.items():
            if spec is None:
                props.pop(name, None)
            elif set(spec) == {"name"}:
                props[spec["name"]] = props.pop(name)
            else:
                props[name] = {"id": new_id()[:4], "type": next(iter(spec))}
        return {}

    def query_all(self, data_source_id, *, filter=None, sorts=None, page_size=100, limit=None):
        self._tick("query", ds=data_source_id)
        rows = [
            p
            for p in self.pages.values()
            if p["parent"].get("data_source_id") == data_source_id and not p["in_trash"]
        ]
        if filter and "rich_text" in filter:
            want = filter["rich_text"]["equals"]
            rows = [
                p
                for p in rows
                if "".join(
                    t["plain_text"]
                    for t in p["properties"].get(filter["property"], {}).get("rich_text", [])
                )
                == want
            ]
        return iter(copy.deepcopy(rows))

    def rows(self, title: str) -> list[dict[str, Any]]:
        ds = next(k for k, v in self.data_sources.items() if v["title"] == title)
        return [
            p
            for p in self.pages.values()
            if p["parent"].get("data_source_id") == ds and not p["in_trash"]
        ]

    # blocks ------------------------------------------------------------------------
    def list_children(self, block_id):
        self._tick("list_children")
        return copy.deepcopy(self.blocks.get(block_id, []))

    def append_children(self, block_id, children):
        self._tick("append_children")
        out = []
        for c in children:
            blk = {"id": new_id(), **copy.deepcopy(c)}
            body = blk.get(blk["type"], {})
            for part in body.get("rich_text", []):
                part["plain_text"] = part["text"]["content"]
            self.blocks.setdefault(block_id, []).append(blk)
            out.append(blk)
        return out

    def update_block(self, block_id, body):
        self._tick("update_block")
        for blocks in self.blocks.values():
            for b in blocks:
                if b["id"] == block_id:
                    kind = next(iter(body))
                    b[kind] = copy.deepcopy(body[kind])
                    for part in b[kind].get("rich_text", []):
                        part["plain_text"] = part["text"]["content"]
        return {}

    def delete_block(self, block_id):
        self._tick("delete_block")
        for key, blocks in self.blocks.items():
            self.blocks[key] = [b for b in blocks if b["id"] != block_id]

    def upload_file(self, filename, content, content_type):
        self._tick("upload_file")
        self.uploads.append(filename)
        return new_id()

    def create_view(self, body):
        self._tick("create_view")
        self.views.append(body)
        return {"id": new_id()}


class FakeSplitwiseServer:
    """Serves get_current_user / get_groups / get_expenses over httpx.MockTransport,
    honouring updated_after, updated_before, dated_after and limit/offset."""

    def __init__(
        self,
        user_id: int,
        expenses: list[dict[str, Any]] | None = None,
        groups: list[dict[str, Any]] | None = None,
    ) -> None:
        self.user_id = user_id
        self.expenses = expenses or []
        self.groups = groups or []
        self.requests: list[httpx.Request] = []
        self.fail_next: int | None = None  # HTTP status to return on the next call

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.fail_next:
            status, self.fail_next = self.fail_next, None
            return httpx.Response(status, json={"error": "simulated"})
        path = req.url.path.rsplit("/", 1)[-1]
        if path == "get_current_user":
            return httpx.Response(200, json={"user": {"id": self.user_id}})
        if path == "get_groups":
            return httpx.Response(200, json={"groups": self.groups})
        if path == "get_expenses":
            p = req.url.params
            items = sorted(self.expenses, key=lambda e: (e["updated_at"], e["id"]), reverse=True)

            def ts(v: str) -> datetime:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))

            if "updated_after" in p:
                items = [e for e in items if ts(e["updated_at"]) > ts(p["updated_after"])]
            if "updated_before" in p:
                items = [e for e in items if ts(e["updated_at"]) <= ts(p["updated_before"])]
            if "dated_after" in p:
                items = [e for e in items if ts(e["date"]) >= ts(p["dated_after"])]
            offset, limit = int(p.get("offset", 0)), int(p.get("limit", 20))
            return httpx.Response(200, json={"expenses": items[offset : offset + limit]})
        return httpx.Response(404, json={"error": "not found"})

    def client(self, page_size: int = 3) -> SplitwiseClient:
        http = HttpClient(
            "Splitwise",
            BASE_URL,
            {},
            transport=httpx.MockTransport(self.handler),
            sleep=lambda s: None,
            policy=RetryPolicy(max_attempts=2),
        )
        return SplitwiseClient("test", http=http, page_size=page_size)


def api_error(status: int, message: str = "simulated") -> ApiError:
    return ApiError("Notion", status, message, retryable=status in (429, 500, 502, 503, 504))
