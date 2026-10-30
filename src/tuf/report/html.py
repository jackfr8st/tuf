"""Standalone local HTML report (``tuf report``). A single self-contained file with inline
SVG charts; nothing is uploaded anywhere. Treat the output as private financial data."""

from __future__ import annotations

from html import escape

from tuf.analytics.charts import category_color, render_all
from tuf.analytics.engine import STATUS_EMOJI, Dashboard
from tuf.money import fmt

_CSS = """
:root { --bg:#f9f9f7; --card:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --line:#e1e0d9; --ring:rgba(11,11,11,.10); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0d0d0d;
  --card:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781; --line:#2c2c2a;
  --ring:rgba(255,255,255,.10); } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:1120px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 12px; }
.sub { color:var(--ink2); margin:0 0 20px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
.tile { background:var(--card); border:1px solid var(--ring); border-radius:12px; padding:14px; }
.tile .label { color:var(--ink2); font-size:13px; } .tile .value { font-size:24px;
  font-weight:600; margin-top:2px; } .tile .detail { color:var(--muted); font-size:12.5px; }
.hero .value { font-size:34px; }
.charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));
  gap:12px; } .charts svg { width:100%; height:auto; display:block; }
ul.insights { padding-left:0; list-style:none; } ul.insights li { margin:6px 0; }
table { width:100%; border-collapse:collapse; background:var(--card); border-radius:12px;
  overflow:hidden; border:1px solid var(--ring); font-variant-numeric:tabular-nums; }
th,td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); font-size:14px; }
th { color:var(--ink2); font-weight:500; } td.num { text-align:right; }
.swatch { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:8px; }
.note { color:var(--muted); font-size:13px; margin-top:32px; }
.scroll { overflow-x:auto; }
"""

_ICON = {"good": "✅", "info": "💡", "warn": "⚠️", "alert": "🔴"}


def _tile(label: str, value: str, detail: str = "", hero: bool = False) -> str:
    return (
        f'<div class="tile{" hero" if hero else ""}"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div><div class="detail">{escape(detail)}</div>'
        "</div>"
    )


def render_report(d: Dashboard, title: str = "TUF spending report", demo: bool = False) -> str:
    cur, m, b = d.currency, d.month, d.month.budget
    svgs = render_all(d)
    tiles = [
        _tile("Spent this month", fmt(m.spent, cur), f"{m.expense_count} expenses", hero=True),
        _tile(
            "Monthly budget",
            fmt(b.limit, cur) if b.limit else "Not set",
            ""
            if b.remaining is None
            else f"{fmt(b.remaining, cur)} left"
            if b.remaining >= 0
            else f"{fmt(-b.remaining, cur)} over",
        ),
        _tile(
            "Budget used",
            f"{STATUS_EMOJI[b.status]} {b.utilization}%" if b.limit else "—",
            f"{m.days_in_month - m.elapsed_days} days left",
        ),
        _tile("Daily average", fmt(m.daily_average, cur), f"over {m.elapsed_days} days"),
        _tile(
            "Projected month-end",
            fmt(m.projected, cur),
            "estimate at current pace" if m.projection_is_estimate else "final",
        ),
        _tile("Paid for others", fmt(m.advanced_for_others, cur), "not counted as spending"),
    ]
    parts = [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>{escape(title)}</h1><p class='sub'>{escape(m.month)} · as of "
        f"{d.as_of:%b %d, %Y} · amounts in {cur}"
        f"{' · fictional demo data' if demo else ''}</p>",
        f"<div class='tiles'>{''.join(tiles)}</div>",
    ]
    if d.insights:
        parts.append(
            "<h2>Insights</h2><ul class='insights'>"
            + "".join(f"<li>{_ICON.get(i.level, '•')} {escape(i.text)}</li>" for i in d.insights)
            + "</ul>"
        )
    parts.append("<h2>Charts</h2><div class='charts'>" + "".join(svgs.values()) + "</div>")
    rows = "".join(
        f"<tr><td><span class='swatch' style='background:{category_color(c.name)}'></span>"
        f"{escape(c.name)}</td><td class='num'>{fmt(c.amount, cur)}</td>"
        f"<td class='num'>{c.share_pct if c.share_pct is not None else '—'}%</td>"
        f"<td class='num'>{fmt(c.budget.limit, cur) if c.budget else '—'}</td>"
        f"<td>{STATUS_EMOJI[c.budget.status] + ' ' + c.budget.gauge if c.budget else ''}</td></tr>"
        for c in d.categories
    )
    parts.append(
        "<h2>Categories</h2><div class='scroll'><table><tr><th>Category</th>"
        "<th>Spent</th><th>Share</th><th>Budget</th><th>Gauge</th></tr>"
        f"{rows}</table></div>"
    )
    if d.trips:
        trows = "".join(
            f"<tr><td>{escape(t.name)}</td><td>{t.start or ''} – {t.end or ''}</td>"
            f"<td class='num'>{fmt(t.total, cur)}</td>"
            f"<td class='num'>{fmt(t.budget, cur) if t.budget else '—'}</td>"
            f"<td>{escape(', '.join(f'{c} {fmt(v, cur)}' for c, v in t.by_category[:3]))}</td>"
            "</tr>"
            for t in d.trips
        )
        parts.append(
            "<h2>Trips</h2><div class='scroll'><table><tr><th>Trip</th><th>Dates</th>"
            f"<th>Spent</th><th>Budget</th><th>Top categories</th></tr>{trows}</table>"
            "</div>"
        )
    if d.recurring:
        rrows = "".join(
            f"<tr><td>{escape(r.merchant)}</td><td>{escape(r.category)}</td>"
            f"<td class='num'>{fmt(r.monthly_average, cur)}</td><td>{r.months}</td>"
            "</tr>"
            for r in d.recurring
        )
        parts.append(
            "<h2>Recurring purchases</h2><div class='scroll'><table><tr><th>Merchant"
            "</th><th>Category</th><th>Avg / month</th><th>Months (of 6)</th></tr>"
            f"{rrows}</table></div>"
        )
    recent = "".join(
        f"<tr><td>{e.date:%b %d}</td><td>{escape(e.title)}</td>"
        f"<td>{escape(e.primary)}</td><td>{escape(e.txn_type.value)}</td>"
        f"<td class='num'>{fmt(e.spending, cur) if e.counted else '—'}</td></tr>"
        for e in d.recent
    )
    parts.append(
        "<h2>Recent expenses</h2><div class='scroll'><table><tr><th>Date</th>"
        "<th>Expense</th><th>Category</th><th>Type</th><th>Your spending</th></tr>"
        f"{recent}</table></div>"
    )
    parts.append(
        "<p class='note'>Spending is your share of each expense, computed with exact "
        "decimal arithmetic from Splitwise data. Settlements, deleted expenses and money "
        "paid on behalf of others are excluded. Projections assume the current daily "
        "pace continues and are estimates, not certainties.</p></main></body></html>"
    )
    return "".join(parts)
