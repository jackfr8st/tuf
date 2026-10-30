"""Command-line interface.

tuf check                 validate configuration and every connection (read-only)
tuf setup [--views]       create the Notion databases + dashboard under your root page
tuf sync [options]        synchronize Splitwise -> Notion and refresh the dashboard
tuf reclassify            re-run categorization for every unlocked expense
tuf report -o FILE        write a private local HTML report from your Notion data
tuf demo -o DIR           fictional demo report/charts (no credentials needed)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tuf import __version__
from tuf.config import Config, ConfigError, load_config
from tuf.http import ApiError
from tuf.logging_utils import configure_logging

if TYPE_CHECKING:
    from tuf.categorization.ai import AIClassifier
    from tuf.finance.fx import RateProvider
    from tuf.notion.client import NotionClient
    from tuf.splitwise.client import SplitwiseClient

log = logging.getLogger("tuf")

EXIT_OK, EXIT_SYNC_ERRORS, EXIT_CONFIG, EXIT_CONNECTION = 0, 1, 2, 3


def _clients(cfg: Config) -> tuple[SplitwiseClient, NotionClient]:
    from tuf.notion.client import NotionClient
    from tuf.splitwise.client import SplitwiseClient

    return SplitwiseClient(cfg.splitwise_api_key), NotionClient(cfg.notion_api_key)


def _ai(cfg: Config) -> AIClassifier | None:
    if not cfg.ai_enabled or not cfg.gemini_api_key:
        return None
    from tuf.categorization.ai import AIUnavailable, GeminiClassifier

    try:
        return GeminiClassifier(cfg.gemini_api_key, cfg.gemini_model)
    except AIUnavailable as exc:
        log.warning("AI disabled for this run: %s", exc)
        return None


def _rates(cfg: Config) -> RateProvider:
    from tuf.finance.fx import FrankfurterRates, NoRates

    return FrankfurterRates() if cfg.fx_enabled else NoRates()


# ------------------------------------------------------------------------------ commands


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    from tuf.notion.store import check_schema, discover

    ok = True

    def report(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {label}{': ' + detail if detail else ''}")

    print("Configuration")
    report(True, "environment variables valid", repr(cfg))
    sw, notion = _clients(cfg)
    print("Splitwise")
    try:
        user = sw.current_user()
        report(True, "authenticated", f"user id {user.id}")
        report(True, "groups readable", f"{len(sw.groups())} groups")
    except ApiError as exc:
        report(False, "authentication failed", f"{exc}. Check SPLITWISE_API_KEY.")
    print("Notion")
    try:
        notion.me()
        report(True, "integration token valid")
        ws = discover(notion, cfg.notion_root_page_id)
        report(True, "root page shared with the integration")
        missing = ws.missing()
        report(
            not missing,
            "TUF workspace present",
            f"missing {', '.join(missing)} - run `tuf setup`" if missing else "",
        )
        if not missing:
            issues = check_schema(notion, ws, cfg.reporting_currency, apply=False)
            report(
                not issues,
                "schema compatible",
                "; ".join(map(str, issues))
                + (
                    " (sync adds missing properties)"
                    if all(i.problem == "missing" for i in issues)
                    else ""
                )
                if issues
                else "",
            )
    except ApiError as exc:
        hint = (
            "Share the root page with your integration (••• menu → Connections)"
            if exc.status in (403, 404)
            else "Check NOTION_API_KEY."
        )
        report(False, "access failed", f"{exc}. {hint}")
    print("AI (optional)")
    if not cfg.ai_enabled:
        report(True, "disabled", "rule-based classification only")
    else:
        from tuf.categorization.ai import AIUnavailable, GeminiClassifier

        try:
            model = GeminiClassifier(cfg.gemini_api_key or "", cfg.gemini_model).check()
            report(True, "Gemini reachable", model)
        except AIUnavailable as exc:
            report(
                False,
                "Gemini unavailable",
                f"{exc}. TUF still works without AI (set AI_ENABLED=false to silence this).",
            )
    print("Currency conversion")
    report(
        True,
        "enabled (frankfurter.dev)" if cfg.fx_enabled else "disabled",
        f"reporting currency {cfg.reporting_currency}",
    )
    print()
    print("All checks passed." if ok else "Some checks failed - see messages above.")
    return EXIT_OK if ok else EXIT_CONNECTION


def cmd_setup(cfg: Config, args: argparse.Namespace) -> int:
    from tuf.notion.store import setup_workspace
    from tuf.notion.views import create_native_views

    _, notion = _clients(cfg)
    ws, actions = setup_workspace(notion, cfg.notion_root_page_id, cfg.reporting_currency)
    for a in actions or ["workspace already set up; nothing to create"]:
        print(f"  • {a}")
    if args.views:
        for line in create_native_views(notion, ws):
            print(f"  • {line}")
    print("\nNext: run `tuf sync` (first run imports your history).")
    return EXIT_OK


def cmd_sync(cfg: Config, args: argparse.Namespace, *, reclassify: bool = False) -> int:
    from tuf.sync.engine import SyncEngine, SyncOptions
    from tuf.sync.lock import LockHeld, run_lock

    sw, notion = _clients(cfg)
    opts = SyncOptions(
        full=getattr(args, "full", False),
        dry_run=args.dry_run,
        since=getattr(args, "since", None),
        no_ai=args.no_ai,
        reclassify=reclassify,
        force_dashboard=getattr(args, "force_dashboard", False),
        force_reconcile=getattr(args, "force_reconcile", False),
    )
    try:
        with run_lock():
            summary, _ = SyncEngine(cfg, sw, notion, rates=_rates(cfg), ai=_ai(cfg)).run(opts)
    except LockHeld as exc:
        log.error("%s", exc)
        return EXIT_SYNC_ERRORS
    for line in summary.lines():
        print(line)
    if args.summary_json:
        Path(args.summary_json).write_text(
            json.dumps(summary.__dict__, default=str, indent=2), encoding="utf-8"
        )
    return EXIT_OK if summary.ok else EXIT_SYNC_ERRORS


def cmd_report(cfg: Config, args: argparse.Namespace) -> int:
    from tuf.analytics.engine import AnalyticsInput, Thresholds, build_dashboard
    from tuf.notion.store import NotionStore, discover
    from tuf.report.html import render_report

    _, notion = _clients(cfg)
    ws = discover(notion, cfg.notion_root_page_id)
    store = NotionStore(notion, ws)
    tax = store.load_taxonomy()
    trips = store.load_trips()
    entries = store.load_expenses(trips)
    today = datetime.now(UTC).astimezone(cfg.tz).date()
    dash = build_dashboard(
        AnalyticsInput(
            entries,
            today,
            cfg.reporting_currency,
            cfg.monthly_budget,
            {n: c.monthly_budget for n, c in tax.primaries.items() if c.monthly_budget},
            {k: c.monthly_budget for k, c in tax.subcategories.items() if c.monthly_budget},
            trips,
            Thresholds(cfg.budget_amber_pct, cfg.budget_red_pct),
        )
    )
    out = Path(args.output)
    out.write_text(render_report(dash), encoding="utf-8")
    print(f"Wrote {out} (private: contains your financial data; do not publish it).")
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    from tuf.analytics.charts import render_all
    from tuf.demo import demo_dashboard, demo_expenses
    from tuf.report.html import render_report

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    dash, _, _ = demo_dashboard()
    (out / "demo-report.html").write_text(
        render_report(dash, "TUF demo report", demo=True), encoding="utf-8"
    )
    for name, svg in render_all(dash).items():
        (out / f"demo-{name}.svg").write_text(svg, encoding="utf-8")
    if args.sample_json:
        Path(args.sample_json).write_text(json.dumps(demo_expenses(), indent=1), encoding="utf-8")
    print(f"Wrote fictional demo report and charts to {out}/")
    return EXIT_OK


# ------------------------------------------------------------------------------ main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tuf",
        description="TUF: Splitwise -> Notion personal "
        "spending analytics. Not affiliated with Splitwise or Notion.",
    )
    p.add_argument("--version", action="version", version=f"tuf {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate configuration and connections (read-only)")
    s = sub.add_parser("setup", help="create TUF databases and dashboard in Notion")
    s.add_argument(
        "--views",
        action="store_true",
        help="also try to create native Notion views (1 chart on Free plan)",
    )

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--dry-run", action="store_true", help="compute everything, write nothing")
        sp.add_argument("--no-ai", action="store_true", help="rule-based classification only")
        sp.add_argument("--summary-json", metavar="FILE", help="write the run summary as JSON")

    s = sub.add_parser("sync", help="synchronize Splitwise to Notion")
    common(s)
    s.add_argument(
        "--full", action="store_true", help="fetch all history and reconcile removed expenses"
    )
    s.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="backfill expenses dated on/after this date",
    )
    s.add_argument("--force-dashboard", action="store_true", help="re-render the dashboard")
    s.add_argument(
        "--force-reconcile",
        action="store_true",
        help="allow a full sync to mark many expenses as removed",
    )
    s = sub.add_parser("reclassify", help="re-run categorization for all unlocked expenses")
    common(s)
    s = sub.add_parser("report", help="write a local HTML report from Notion data")
    s.add_argument("-o", "--output", default="tuf-report.html")
    s = sub.add_parser("demo", help="fictional demo report (no credentials needed)")
    s.add_argument("-o", "--output", default="tuf-demo")
    s.add_argument("--sample-json", metavar="FILE", help="also write fictional Splitwise JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        configure_logging()
        return cmd_demo(args)
    try:
        cfg = load_config()
    except ConfigError as exc:
        configure_logging()
        print(str(exc), file=sys.stderr)
        print(
            "\nSee docs/CONFIGURATION.md. Copy .env.example to .env to get started.",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    configure_logging(cfg.log_level, cfg.log_format, cfg.secrets)
    handlers = {"check": cmd_check, "setup": cmd_setup, "sync": cmd_sync, "report": cmd_report}
    try:
        if args.command == "reclassify":
            return cmd_sync(cfg, args, reclassify=True)
        return handlers[args.command](cfg, args)
    except ApiError as exc:
        log.error("%s", exc)
        if exc.status in (401, 403):
            log.error("Authentication/permission problem: run `tuf check` for details.")
        return EXIT_CONNECTION
    except Exception as exc:
        from tuf.notion.store import WorkspaceError

        if isinstance(exc, WorkspaceError):
            log.error("%s", exc)
            return EXIT_CONNECTION
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
