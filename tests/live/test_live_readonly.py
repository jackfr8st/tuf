"""Read-only smoke tests against the real services (skipped unless TUF_LIVE_TESTS=1).

They use the credentials in your environment / .env and never write anything:
Splitwise identity + groups, Notion token + root page + TUF schema.
"""

import pytest

from tuf.config import load_config
from tuf.notion.client import NotionClient
from tuf.notion.store import check_schema, discover
from tuf.splitwise.client import SplitwiseClient

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def cfg():  # type: ignore[no-untyped-def]
    return load_config()


def test_splitwise_identity_and_groups(cfg) -> None:  # type: ignore[no-untyped-def]
    sw = SplitwiseClient(cfg.splitwise_api_key)
    user = sw.current_user()
    assert user.id > 0
    sw.groups()


def test_notion_workspace_schema(cfg) -> None:  # type: ignore[no-untyped-def]
    notion = NotionClient(cfg.notion_api_key)
    notion.me()
    ws = discover(notion, cfg.notion_root_page_id)
    if ws.missing():
        pytest.skip(f"run `tuf setup` first (missing {ws.missing()})")
    issues = check_schema(notion, ws, cfg.reporting_currency, apply=False)
    assert all(i.problem == "missing" for i in issues), issues
