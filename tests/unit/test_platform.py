"""Config, logging, locking, Gemini adapter, charts, dashboard blocks, CLI."""

import io
import logging
import xml.etree.ElementTree as ET

import pytest

from tuf.analytics.charts import render_all
from tuf.categorization.ai import (
    AIItem,
    AIQuotaExceeded,
    AIRequest,
    AIUnavailable,
    GeminiClassifier,
)
from tuf.categorization.taxonomy import Taxonomy
from tuf.cli import main
from tuf.config import ConfigError, load_config, parse_notion_id
from tuf.demo import demo_dashboard
from tuf.logging_utils import RedactingFilter, configure_logging
from tuf.notion.dashboard import build_blocks, content_hash
from tuf.notion.store import _same
from tuf.sync.lock import LockHeld, run_lock

BASE = {
    "SPLITWISE_API_KEY": "sw_abcdefghijkl",
    "NOTION_API_KEY": "ntn_abcdefghijklmnop",
    "NOTION_ROOT_PAGE_ID": "https://www.notion.so/My-Budget-0123456789abcdef0123456789abcdef",
}


def test_config_defaults_and_url_parsing():
    cfg = load_config(BASE)
    assert cfg.notion_root_page_id == "01234567-89ab-cdef-0123-456789abcdef"
    assert cfg.reporting_currency == "USD" and not cfg.ai_enabled
    assert "sw_abc" not in repr(cfg)
    assert parse_notion_id("nope") is None


def test_config_ai_enabled_only_with_key():
    assert load_config({**BASE, "GEMINI_API_KEY": "AIzaXYZ"}).ai_enabled
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_config({**BASE, "AI_ENABLED": "true"})


def test_config_reports_all_problems_actionably():
    with pytest.raises(ConfigError) as exc:
        load_config(
            {
                "REPORTING_CURRENCY": "dollars",
                "TIMEZONE": "Mars/Base",
                "MONTHLY_BUDGET": "-5",
                "BUDGET_AMBER_PCT": "95",
                "BACKFILL_START": "x",
                "TRIP_GROUP_PATTERN": "(oops",
                "AI_BATCH_SIZE": "0",
            }
        )
    text = str(exc.value)
    for needle in (
        "SPLITWISE_API_KEY",
        "NOTION_API_KEY",
        "NOTION_ROOT_PAGE_ID",
        "REPORTING_CURRENCY",
        "TIMEZONE",
        "MONTHLY_BUDGET",
        "BUDGET_AMBER_PCT",
        "BACKFILL_START",
        "TRIP_GROUP_PATTERN",
        "AI_BATCH_SIZE",
        "https://secure.splitwise.com/apps",
    ):
        assert needle in text


def test_secrets_are_redacted_from_logs():
    stream = io.StringIO()
    configure_logging("INFO", "json", [BASE["SPLITWISE_API_KEY"]])
    logging.getLogger().handlers[0].stream = stream
    logging.getLogger("t").warning(
        "key=%s auth=Bearer abc.def-123 g=AIzaSyA0123456789012345678901234567890",
        BASE["SPLITWISE_API_KEY"],
    )
    out = stream.getvalue()
    assert "sw_abcdefghijkl" not in out and "abc.def-123" not in out and "AIzaSy" not in out
    assert out.count("[REDACTED]") == 3
    assert RedactingFilter(["ntn_secretvalue"]).redact("x ntn_secretvalue y") == "x [REDACTED] y"


def test_run_lock_blocks_concurrent_runs(tmp_path):
    with (
        run_lock(directory=str(tmp_path)),
        pytest.raises(LockHeld),
        run_lock(directory=str(tmp_path)),
    ):
        pass
    with run_lock(directory=str(tmp_path)):  # released afterwards
        pass


class _Resp:
    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        return self.behaviour()


def _gemini(behaviour):
    g = GeminiClassifier("fake-key", "test-model")

    class _Client:
        models = _Models(behaviour)

    g._client = _Client()
    return g


REQ = AIRequest([AIItem("e0", "zuni")], ["Dining Out", "Other"], {}, [])


def test_gemini_adapter_structured_output_request():
    g = _gemini(lambda: _Resp('{"results": [{"ref": "e0"}]}'))
    assert g.classify(REQ) == [{"ref": "e0"}]
    model, _contents, config = g._client.models.calls[0]
    assert model == "test-model"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["required"] == ["results"]
    assert "untrusted" in config.system_instruction


def test_gemini_adapter_errors_map_to_fallbacks():
    from google.genai import errors

    def quota():
        raise errors.ClientError(429, {"error": {"message": "quota"}})

    def server():
        raise errors.ServerError(503, {"error": {"message": "down"}})

    def network():
        raise ConnectionError("offline")

    with pytest.raises(AIQuotaExceeded):
        _gemini(quota).classify(REQ)
    with pytest.raises(AIUnavailable):
        _gemini(server).classify(REQ)
    with pytest.raises(AIUnavailable):
        _gemini(network).classify(REQ)
    with pytest.raises(AIUnavailable):
        _gemini(lambda: _Resp("not json")).classify(REQ)


def test_charts_are_valid_svg_and_contain_no_scripts():
    dash, _, _ = demo_dashboard()
    svgs = render_all(dash)
    assert set(svgs) == {"category-donut", "budget-pace", "category-budgets", "monthly", "trips"}
    for svg in svgs.values():
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg")
        assert "<script" not in svg and "javascript:" not in svg
        assert len(svg.encode()) < 5 * 1024 * 1024  # Notion Free upload limit


def test_dashboard_blocks_and_hash_stability():
    dash, _, tax = demo_dashboard()
    blocks = build_blocks(dash, tax, {"budget-pace"}, {"bigsur2026": "Big Sur 2026"})
    kinds = [b["type"] for b in blocks]
    assert kinds[0] == "heading_2" and "column_list" in kinds and "image" in kinds
    text = str(blocks)
    assert "sw:" not in text  # no technical identifiers on the dashboard
    assert content_hash(blocks, {}) == content_hash(
        build_blocks(dash, tax, {"budget-pace"}, {"bigsur2026": "Big Sur 2026"}), {}
    )


def test_demo_reconciles():
    dash, ledger, _ = demo_dashboard()
    counted = [e for e in ledger if e.counted and e.date.strftime("%Y-%m") == dash.month.month]
    assert sum(e.spending for e in counted) == dash.month.spent
    assert sum(c.amount for c in dash.categories) == dash.month.spent
    assert all(t.total == sum(v for _, v in t.by_category) for t in dash.trips)


def test_same_property_comparison():
    assert _same({"type": "number", "number": 12.3}, {"number": 12.3})
    assert not _same({"type": "number", "number": 12.3}, {"number": 12.31})
    assert _same({"type": "select", "select": None}, {"select": None})
    assert _same(
        {"type": "rich_text", "rich_text": [{"plain_text": "a"}]},
        {"rich_text": [{"type": "text", "text": {"content": "a"}}]},
    )
    assert not _same(None, {"number": 1})


def test_cli_demo_without_credentials(tmp_path, monkeypatch):
    for key in BASE:
        monkeypatch.delenv(key, raising=False)
    sample = tmp_path / "sample.json"
    assert main(["demo", "-o", str(tmp_path / "out"), "--sample-json", str(sample)]) == 0
    assert (tmp_path / "out" / "demo-report.html").exists()
    assert sample.read_text().startswith("[")


def test_cli_missing_config_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here
    for key in BASE:
        monkeypatch.delenv(key, raising=False)
    assert main(["check"]) == 2
    assert "SPLITWISE_API_KEY is missing" in capsys.readouterr().err


def test_taxonomy_defaults_cover_spec_categories():
    names = Taxonomy.defaults().primary_names()
    assert names == [
        "Dining Out",
        "Groceries",
        "Snacks",
        "Entertainment",
        "Shopping",
        "Transportation",
        "Stay",
        "Health & Insurance",
        "Other",
    ]
