"""Dependency-free SVG charts for the Notion dashboard and the local HTML report.

Design rules (validated reference palette, fixed slot order, color follows the entity):

* Each primary category owns a fixed color slot; categories beyond the 8 slots and
  "Other" are gray, so a filter or a missing category never repaints the others.
* Thin marks: bars <= 24px with 4px rounded data-ends, 2px lines, >= 8px end markers.
* Text uses ink tokens, never series colors. Hairline, recessive gridlines. One y-axis.
* Charts render on their own light card so they stay legible in Notion light and dark
  themes (an <img> cannot follow Notion's theme toggle).
* Every mark has a <title> so browsers show a tooltip in the HTML report.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from tuf.analytics.engine import Dashboard
from tuf.money import fmt

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BORDER = "rgba(11,11,11,0.10)"
DEEMPH = "#b7d3f6"  # sequential blue step 150 (de-emphasised columns)
ACCENT = "#2a78d6"
STATUS = {"Green": "#0ca30c", "Amber": "#fab219", "Red": "#d03b3b", "Over": "#d03b3b"}

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CATEGORY_ORDER = [
    "Dining Out",
    "Groceries",
    "Snacks",
    "Entertainment",
    "Shopping",
    "Transportation",
    "Stay",
    "Health & Insurance",
]
OTHER_GRAY = "#a8a7a1"
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def category_color(name: str) -> str:
    try:
        return SLOTS[CATEGORY_ORDER.index(name)]
    except ValueError:
        return OTHER_GRAY


def _compact(v: Decimal | float, currency: str) -> str:
    x = float(v)
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥"}.get(currency, "")
    a = abs(x)
    if a >= 1_000_000:
        s = f"{a / 1_000_000:.1f}M"
    elif a >= 1000:
        s = f"{a / 1000:.1f}K".replace(".0K", "K")
    else:
        s = f"{a:.0f}"
    return f"{'-' if x < 0 else ''}{sym}{s}"


def _nice_max(v: float, ticks: int = 4) -> float:
    """Smallest 'round' axis maximum >= v whose tick step (max / ticks) is 1, 2, 2.5 or 5
    times a power of ten, so every gridline label is a clean number."""
    if v <= 0:
        return float(ticks)
    raw = v / ticks
    exp = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * exp:
            return float(m * exp * ticks)
    return float(10 * exp * ticks)


def _axis(v: float) -> tuple[float, int]:
    """(max, interval count) with the least headroom among 4- or 5-interval round axes."""
    return min(((_nice_max(v, n), n) for n in (4, 5)), key=lambda x: (x[0], x[1]))


def _svg(width: int, height: int, body: str, title: str, subtitle: str = "") -> str:
    sub = (
        f'<text x="20" y="50" font-size="12.5" fill="{INK_2}">{escape(subtitle)}</text>'
        if subtitle
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family=\'{FONT}\' role="img" '
        f'aria-label="{escape(title)}">'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" '
        f'fill="{SURFACE}" stroke="{BORDER}"/>'
        f'<text x="20" y="30" font-size="15" font-weight="600" fill="{INK}">{escape(title)}</text>'
        f"{sub}{body}</svg>"
    )


def _bar_path(
    x: float, y: float, w: float, h: float, r: float = 4, horizontal: bool = False
) -> str:
    """Rect with rounded data-end only (right end if horizontal, top if vertical)."""
    if w <= 0 or h <= 0:
        return ""
    if horizontal:
        r = min(r, w, h / 2)
        return (
            f"M{x:.1f},{y:.1f}H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}"
            f"V{y + h - r:.1f}Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f}"
            f"H{x:.1f}Z"
        )
    r = min(r, h, w / 2)
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


# --------------------------------------------------------------------------- donut


def category_donut(d: Dashboard) -> str | None:
    items = [(c.name, float(c.amount)) for c in d.categories if c.amount > 0]
    if not items:
        return None
    slotted = [(n, v) for n, v in items if n in CATEGORY_ORDER]
    rest = sum(v for n, v in items if n not in CATEGORY_ORDER)
    slotted.sort(key=lambda x: CATEGORY_ORDER.index(x[0]))
    if rest > 0:
        slotted.append(("Other", rest))
    total = sum(v for _, v in slotted)
    width, height = 720, 330
    cx, cy, r_out, r_in = 170, 185, 118, 78
    body, angle = [], -math.pi / 2
    gap = 0.012 if len(slotted) > 1 else 0
    for name, v in slotted:
        sweep = 2 * math.pi * v / total
        a0, a1 = angle + gap / 2, angle + sweep - gap / 2
        if len(slotted) == 1:
            body.append(
                f'<circle cx="{cx}" cy="{cy}" r="{(r_out + r_in) / 2}" fill="none" '
                f'stroke="{category_color(name)}" stroke-width="{r_out - r_in}">'
                f"<title>{escape(name)}: {fmt(Decimal(str(v)), d.currency)}</title>"
                "</circle>"
            )
        else:
            large = 1 if a1 - a0 > math.pi else 0
            p = (
                f"M{cx + r_out * math.cos(a0):.2f},{cy + r_out * math.sin(a0):.2f}"
                f"A{r_out},{r_out} 0 {large} 1 {cx + r_out * math.cos(a1):.2f},"
                f"{cy + r_out * math.sin(a1):.2f}"
                f"L{cx + r_in * math.cos(a1):.2f},{cy + r_in * math.sin(a1):.2f}"
                f"A{r_in},{r_in} 0 {large} 0 {cx + r_in * math.cos(a0):.2f},"
                f"{cy + r_in * math.sin(a0):.2f}Z"
            )
            body.append(
                f'<path d="{p}" fill="{category_color(name)}"><title>{escape(name)}: '
                f"{fmt(Decimal(str(v)), d.currency)} ({v / total:.0%})</title></path>"
            )
        angle += sweep
    body.append(
        f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="22" '
        f'font-weight="600" fill="{INK}">{escape(fmt(d.month.spent, d.currency))}</text>'
    )
    body.append(
        f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" font-size="12" '
        f'fill="{INK_2}">spent this month</text>'
    )
    # legend with values (identity never color-alone)
    ly = 82
    for name, v in sorted(slotted, key=lambda x: -x[1]):
        body.append(
            f'<rect x="340" y="{ly - 10}" width="12" height="12" rx="3" '
            f'fill="{category_color(name)}"/>'
        )
        body.append(f'<text x="360" y="{ly}" font-size="13" fill="{INK}">{escape(name)}</text>')
        body.append(
            f'<text x="600" y="{ly}" font-size="13" fill="{INK}" text-anchor="end">'
            f"{escape(fmt(Decimal(str(v)), d.currency))}</text>"
        )
        body.append(
            f'<text x="696" y="{ly}" font-size="13" fill="{INK_2}" text-anchor="end">'
            f"{v / total:.0%}</text>"
        )
        ly += 26
    return _svg(
        width,
        height,
        "".join(body),
        "Spending by category",
        f"{d.month.month} · {d.month.expense_count} expenses",
    )


# --------------------------------------------------------------------------- pace


def budget_pace(d: Dashboard) -> str | None:
    if not d.cumulative:
        return None
    width, height = 720, 300
    left, right, top, bottom = 64, 110, 70, 40
    pw, ph = width - left - right, height - top - bottom
    dim = d.month.days_in_month
    limit = float(d.month.budget.limit) if d.month.budget.limit else None
    peak = max(float(d.cumulative[-1][1]), float(d.month.projected), limit or 0)
    ymax, ticks = _axis(peak)

    def x(day: int) -> float:
        return left + (day - 1) / max(1, dim - 1) * pw

    def y(v: float) -> float:
        return top + ph - v / ymax * ph

    body = []
    for i in range(ticks + 1):
        v = ymax * i / ticks
        body.append(
            f'<line x1="{left}" x2="{left + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
            f'stroke="{GRID if i else AXIS}" stroke-width="1"/>'
        )
        body.append(
            f'<text x="{left - 8}" y="{y(v) + 4:.1f}" font-size="11" fill="{MUTED}" '
            f'text-anchor="end">{_compact(v, d.currency)}</text>'
        )
    for day in (1, 8, 15, 22, dim):
        body.append(
            f'<text x="{x(day):.1f}" y="{top + ph + 18}" font-size="11" fill="{MUTED}" '
            f'text-anchor="middle">{day}</text>'
        )
    if limit:
        body.append(
            f'<line x1="{x(1):.1f}" y1="{y(limit / dim):.1f}" x2="{x(dim):.1f}" '
            f'y2="{y(limit):.1f}" stroke="{MUTED}" stroke-width="1.5"/>'
        )
        body.append(
            f'<text x="{x(dim) + 8:.1f}" y="{y(limit) + 4:.1f}" font-size="12" '
            f'fill="{INK_2}">Budget {_compact(limit, d.currency)}</text>'
        )
    last_day = len(d.cumulative)
    last_v = float(d.cumulative[-1][1])
    if d.month.projection_is_estimate and last_day < dim:
        proj = float(d.month.projected)
        body.append(
            f'<line x1="{x(last_day):.1f}" y1="{y(last_v):.1f}" x2="{x(dim):.1f}" '
            f'y2="{y(proj):.1f}" stroke="{ACCENT}" stroke-width="2" opacity="0.35" '
            f'stroke-linecap="round"><title>Projection (linear pace): '
            f"{fmt(d.month.projected, d.currency)}</title></line>"
        )
        label_y = y(proj) + 4
        if limit and abs(y(proj) - y(limit)) < 16:
            label_y = y(proj) + (18 if proj < limit else -10)
        body.append(
            f'<text x="{x(dim) + 8:.1f}" y="{label_y:.1f}" font-size="12" '
            f'fill="{INK_2}">Projected {_compact(proj, d.currency)}</text>'
        )
    pts = " ".join(f"{x(i + 1):.1f},{y(float(v)):.1f}" for i, (_, v) in enumerate(d.cumulative))
    area = f"{x(1):.1f},{y(0):.1f} {pts} {x(last_day):.1f},{y(0):.1f}"
    body.append(f'<polygon points="{area}" fill="{ACCENT}" opacity="0.10"/>')
    body.append(
        f'<polyline points="{pts}" fill="none" stroke="{ACCENT}" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    for i, (when, amount) in enumerate(d.cumulative):
        body.append(
            f'<circle cx="{x(i + 1):.1f}" cy="{y(float(amount)):.1f}" r="7" fill="transparent">'
            f"<title>{when:%b %d}: {fmt(amount, d.currency)} cumulative</title></circle>"
        )
    body.append(
        f'<circle cx="{x(last_day):.1f}" cy="{y(last_v):.1f}" r="4.5" fill="{ACCENT}" '
        f'stroke="{SURFACE}" stroke-width="2"/>'
    )
    body.append(
        f'<text x="{x(last_day):.1f}" y="{y(last_v) - 12:.1f}" font-size="12" '
        f'font-weight="600" fill="{INK}" text-anchor="middle">'
        f"{escape(fmt(d.cumulative[-1][1], d.currency))}</text>"
    )
    sub = "Cumulative spending vs. an even budget pace"
    if d.month.projection_is_estimate:
        sub += " · faded line = projection, not a certainty"
    return _svg(width, height, "".join(body), "Budget pace this month", sub)


# --------------------------------------------------------------------------- bars


def _hbars(
    title: str,
    subtitle: str,
    rows: Sequence[tuple[str, Decimal, Decimal | None, str]],
    currency: str,
) -> str | None:
    """Horizontal bars: (label, value, budget-or-None, color). Budget drawn as a tick."""
    rows = [r for r in rows if r[1] > 0 or r[2]]
    if not rows:
        return None
    width = 720
    left, right, top, row_h = 170, 120, 70, 34
    height = top + row_h * len(rows) + 24
    pw = width - left - right
    vmax = _nice_max(max(max(float(v), float(b or 0)) for _, v, b, _ in rows))
    body = []
    for i, (label, value, budget, color) in enumerate(rows):
        yc = top + i * row_h + row_h / 2
        w = max(0.0, float(value)) / vmax * pw
        body.append(
            f'<text x="{left - 10}" y="{yc + 4:.1f}" font-size="13" fill="{INK}" '
            f'text-anchor="end">{escape(label[:24])}</text>'
        )
        body.append(
            f'<rect x="{left}" y="{yc - 9:.1f}" width="{pw}" height="18" rx="4" '
            f'fill="{GRID}" opacity="0.45"/>'
        )
        tip = f"{label}: {fmt(value, currency)}"
        if budget:
            tip += f" of {fmt(budget, currency)} budget"
        body.append(
            f'<path d="{_bar_path(left, yc - 9, w, 18, horizontal=True)}" fill="{color}">'
            f"<title>{escape(tip)}</title></path>"
        )
        if budget:
            bx = left + float(budget) / vmax * pw
            body.append(
                f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{yc - 13:.1f}" y2="{yc + 13:.1f}" '
                f'stroke="{INK}" stroke-width="2" stroke-linecap="round"/>'
            )
        text = fmt(value, currency)
        if budget and budget > 0:
            text += f" · {float(value) / float(budget):.0%}"
        body.append(
            f'<text x="{left + pw + 10}" y="{yc + 4:.1f}" font-size="12.5" '
            f'fill="{INK}">{escape(text)}</text>'
        )
    return _svg(width, height, "".join(body), title, subtitle)


def category_budgets(d: Dashboard) -> str | None:
    rows = [
        (c.name, c.amount, c.budget.limit if c.budget else None, category_color(c.name))
        for c in d.categories
    ]
    has_budget = any(r[2] for r in rows)
    sub = (
        "Bars = spent this month · black tick = monthly budget"
        if has_budget
        else "Spent this month (set Monthly Budget in TUF Categories to see limits)"
    )
    return _hbars("Categories vs. budget", sub, rows, d.currency)


def trips_chart(d: Dashboard) -> str | None:
    trips = d.trips[:6]
    rows = [(t.name, t.total, t.budget, SLOTS[6]) for t in trips]
    return (
        _hbars("Trips", "Personal spending per trip · black tick = trip budget", rows, d.currency)
        if rows
        else None
    )


# --------------------------------------------------------------------------- months


def monthly_columns(d: Dashboard) -> str | None:
    data = d.monthly
    if not any(v > 0 for _, v in data):
        return None
    width, height = 720, 280
    left, right, top, bottom = 64, 24, 70, 40
    pw, ph = width - left - right, height - top - bottom
    ymax, ticks = _axis(max(float(v) for _, v in data))
    band = pw / len(data)
    bw = min(24.0, band * 0.6)
    body = []
    for i in range(ticks + 1):
        v = ymax * i / ticks
        yy = top + ph - v / ymax * ph
        body.append(
            f'<line x1="{left}" x2="{left + pw}" y1="{yy:.1f}" y2="{yy:.1f}" '
            f'stroke="{GRID if i else AXIS}" stroke-width="1"/>'
        )
        body.append(
            f'<text x="{left - 8}" y="{yy + 4:.1f}" font-size="11" fill="{MUTED}" '
            f'text-anchor="end">{_compact(v, d.currency)}</text>'
        )
    for i, (month, value) in enumerate(data):
        cx = left + band * i + band / 2
        h = float(value) / ymax * ph
        current = i == len(data) - 1
        color = ACCENT if current else DEEMPH
        label = date(int(month[:4]), int(month[5:]), 1).strftime("%b")
        body.append(
            f'<path d="{_bar_path(cx - bw / 2, top + ph - h, bw, h)}" fill="{color}">'
            f"<title>{month}: {escape(fmt(value, d.currency))}"
            f"{' (month to date)' if current else ''}</title></path>"
        )
        body.append(
            f'<text x="{cx:.1f}" y="{top + ph + 18}" font-size="11" fill="{MUTED}" '
            f'text-anchor="middle">{label}</text>'
        )
        if current or value == max(v for _, v in data):
            body.append(
                f'<text x="{cx:.1f}" y="{top + ph - h - 6:.1f}" font-size="11.5" '
                f'fill="{INK}" text-anchor="middle">{_compact(value, d.currency)}</text>'
            )
    return _svg(
        width,
        height,
        "".join(body),
        "Monthly spending",
        "Last 12 months · current month is month-to-date",
    )


def render_all(d: Dashboard) -> dict[str, str]:
    charts = {
        "category-donut": category_donut(d),
        "budget-pace": budget_pace(d),
        "category-budgets": category_budgets(d),
        "monthly": monthly_columns(d),
        "trips": trips_chart(d),
    }
    return {k: v for k, v in charts.items() if v}
