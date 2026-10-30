"""Environment-driven configuration. See docs/CONFIGURATION.md for every variable."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid configuration:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True)
class Config:
    splitwise_api_key: str
    notion_api_key: str
    notion_root_page_id: str
    gemini_api_key: str | None = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    ai_enabled: bool = False
    ai_max_requests: int = 5
    ai_batch_size: int = 20
    ai_min_confidence: Decimal = Decimal("0.6")
    reporting_currency: str = "USD"
    fx_enabled: bool = True
    monthly_budget: Decimal | None = None
    budget_amber_pct: Decimal = Decimal("75")
    budget_red_pct: Decimal = Decimal("90")
    timezone: str = "UTC"
    sync_overlap_hours: int = 48
    backfill_start: date | None = None
    trip_group_pattern: str | None = None
    trip_auto_create: bool = True
    subcategory_min_occurrences: int = 3
    subcategory_auto_approve: bool = False
    dashboard_charts: bool = True
    reconcile_max_removal_ratio: Decimal = Decimal("0.2")
    log_level: str = "INFO"
    log_format: str = "text"
    secrets: tuple[str, ...] = field(default=(), repr=False)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def __repr__(self) -> str:  # never print credentials
        return (
            f"Config(currency={self.reporting_currency}, ai_enabled={self.ai_enabled}, "
            f"model={self.gemini_model}, tz={self.timezone})"
        )


_ID = re.compile(r"([0-9a-f]{32})", re.IGNORECASE)


def parse_notion_id(value: str) -> str | None:
    """Accept a raw id, a dashed UUID, or a full Notion page URL."""
    compact = value.replace("-", "")
    found = _ID.findall(compact)
    if not found:
        return None
    raw = found[-1].lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def load_config(
    env: Mapping[str, str] | None = None, *, require_credentials: bool = True
) -> Config:
    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(override=False)
        except ImportError:  # pragma: no cover
            pass
        env = os.environ
    problems: list[str] = []

    def get(name: str, default: str = "") -> str:
        return (env.get(name) or default).strip()

    def boolean(name: str, default: bool) -> bool:
        raw = get(name).lower()
        if not raw:
            return default
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        problems.append(f"{name} must be true or false (got {raw!r})")
        return default

    def integer(name: str, default: int, lo: int, hi: int) -> int:
        raw = get(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{name} must be a whole number (got {raw!r})")
            return default
        if not lo <= value <= hi:
            problems.append(f"{name} must be between {lo} and {hi} (got {value})")
        return value

    def decimal(name: str, default: Decimal | None, lo: Decimal = Decimal("0")) -> Decimal | None:
        raw = get(name).replace(",", "")
        if not raw:
            return default
        try:
            value = Decimal(raw)
        except InvalidOperation:
            problems.append(f"{name} must be a number (got {raw!r})")
            return default
        if not value.is_finite() or value < lo:
            problems.append(f"{name} must be a non-negative number (got {raw!r})")
        return value

    splitwise = get("SPLITWISE_API_KEY")
    notion = get("NOTION_API_KEY")
    root_raw = get("NOTION_ROOT_PAGE_ID")
    root = parse_notion_id(root_raw) if root_raw else None
    if require_credentials:
        if not splitwise:
            problems.append(
                "SPLITWISE_API_KEY is missing. Create one at "
                "https://secure.splitwise.com/apps (see docs/CONFIGURATION.md)."
            )
        if not notion:
            problems.append(
                "NOTION_API_KEY is missing. Create an internal integration at "
                "https://www.notion.so/profile/integrations."
            )
        if not root_raw:
            problems.append(
                "NOTION_ROOT_PAGE_ID is missing. Paste the URL of the Notion page "
                "you shared with the integration."
            )
    if root_raw and root is None:
        problems.append("NOTION_ROOT_PAGE_ID is not a valid Notion page id or URL.")

    currency = get("REPORTING_CURRENCY", "USD").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        problems.append(f"REPORTING_CURRENCY must be a 3-letter ISO code (got {currency!r})")
    tzname = get("TIMEZONE", "UTC")
    try:
        ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"TIMEZONE {tzname!r} is not a valid IANA zone (e.g. America/Los_Angeles)")
        tzname = "UTC"
    backfill = None
    if get("BACKFILL_START"):
        try:
            backfill = date.fromisoformat(get("BACKFILL_START"))
        except ValueError:
            problems.append("BACKFILL_START must be a date like 2024-01-01")
    pattern = get("TRIP_GROUP_PATTERN") or None
    if pattern:
        try:
            compiled = re.compile(pattern)
            if "name" not in compiled.groupindex:
                problems.append("TRIP_GROUP_PATTERN must contain a (?P<name>...) group")
        except re.error as exc:
            problems.append(f"TRIP_GROUP_PATTERN is not a valid regex: {exc}")
    gemini = get("GEMINI_API_KEY") or None
    ai_enabled = boolean("AI_ENABLED", default=bool(gemini))
    if ai_enabled and not gemini:
        problems.append(
            "AI_ENABLED is true but GEMINI_API_KEY is missing "
            "(set AI_ENABLED=false to use rule-based classification only)"
        )
    amber = decimal("BUDGET_AMBER_PCT", Decimal("75")) or Decimal("75")
    red = decimal("BUDGET_RED_PCT", Decimal("90")) or Decimal("90")
    if amber >= red:
        problems.append("BUDGET_AMBER_PCT must be lower than BUDGET_RED_PCT")
    min_conf = decimal("AI_MIN_CONFIDENCE", Decimal("0.6")) or Decimal("0.6")
    if min_conf > 1:
        problems.append("AI_MIN_CONFIDENCE must be between 0 and 1")
    ratio = decimal("RECONCILE_MAX_REMOVAL_RATIO", Decimal("0.2")) or Decimal("0.2")
    log_level = get("LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        problems.append("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
    log_format = get("LOG_FORMAT", "text").lower()
    if log_format not in ("text", "json"):
        problems.append("LOG_FORMAT must be text or json")

    cfg = Config(
        splitwise_api_key=splitwise,
        notion_api_key=notion,
        notion_root_page_id=root or "",
        gemini_api_key=gemini,
        gemini_model=get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        ai_enabled=ai_enabled and bool(gemini),
        ai_max_requests=integer("AI_MAX_REQUESTS_PER_RUN", 5, 0, 1000),
        ai_batch_size=integer("AI_BATCH_SIZE", 20, 1, 100),
        ai_min_confidence=min_conf,
        reporting_currency=currency,
        fx_enabled=boolean("FX_ENABLED", True),
        monthly_budget=decimal("MONTHLY_BUDGET", None),
        budget_amber_pct=amber,
        budget_red_pct=red,
        timezone=tzname,
        sync_overlap_hours=integer("SYNC_OVERLAP_HOURS", 48, 1, 24 * 60),
        backfill_start=backfill,
        trip_group_pattern=pattern,
        trip_auto_create=boolean("TRIP_AUTO_CREATE", True),
        subcategory_min_occurrences=integer("SUBCATEGORY_MIN_OCCURRENCES", 3, 2, 1000),
        subcategory_auto_approve=boolean("SUBCATEGORY_AUTO_APPROVE", False),
        dashboard_charts=boolean("DASHBOARD_CHARTS", True),
        reconcile_max_removal_ratio=ratio,
        log_level=log_level,
        log_format=log_format,
        secrets=tuple(s for s in (splitwise, notion, gemini or "") if s),
    )
    if problems:
        raise ConfigError(problems)
    return cfg
