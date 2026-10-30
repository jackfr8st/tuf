# Configuration reference

TUF reads environment variables (and a local `.env` file if present). On GitHub Actions put
credentials in **Secrets** and everything else in **Variables**
(Settings → Secrets and variables → Actions). Empty values mean "use the default".
Run `tuf check` after any change: it validates every value and reports all problems at once.

## Credentials (Secrets)

| Variable | Required | How to get it |
|---|---|---|
| `SPLITWISE_API_KEY` | yes | <https://secure.splitwise.com/apps> → *Register your application* (any name/URL, e.g. your repo URL) → copy the **API key**. It acts as you; keep it secret. |
| `NOTION_API_KEY` | yes | <https://www.notion.so/profile/integrations> → *New integration* → Internal → copy the secret. Capabilities: read, update, insert content. |
| `NOTION_ROOT_PAGE_ID` | yes | Create an empty page (e.g. "Budget"), open ••• → *Connections* → add your integration, then paste the page URL (or its 32-character id). |
| `GEMINI_API_KEY` | no | <https://aistudio.google.com/apikey>. Read `docs/PRIVACY.md` first. |

## Money

| Variable | Default | Meaning |
|---|---|---|
| `REPORTING_CURRENCY` | `USD` | ISO code all totals are reported in |
| `MONTHLY_BUDGET` | *(none)* | Overall monthly budget. Category budgets live in the **TUF Categories** database ("Monthly Budget") |
| `FX_ENABLED` | `true` | Convert other currencies via frankfurter.dev. `false` → foreign rows are `Needs FX` and excluded |
| `BUDGET_AMBER_PCT` / `BUDGET_RED_PCT` | `75` / `90` | Gauge thresholds (green below amber; "Over" above 100%) |
| `TIMEZONE` | `UTC` | IANA zone used for dates and month boundaries, e.g. `America/Los_Angeles` |

## AI (optional)

| Variable | Default | Meaning |
|---|---|---|
| `AI_ENABLED` | `true` if `GEMINI_API_KEY` set | `false` = rule-based only |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Any model available to your key; `tuf check` verifies it |
| `AI_MAX_REQUESTS_PER_RUN` | `5` | Hard cap on Gemini calls per run (each call classifies a batch) |
| `AI_BATCH_SIZE` | `20` | Expenses per call |
| `AI_MIN_CONFIDENCE` | `0.6` | Below this, the expense stays "Other" (Fallback) |

## Sync

| Variable | Default | Meaning |
|---|---|---|
| `SYNC_OVERLAP_HOURS` | `48` | Incremental runs re-check this much history before the last change |
| `BACKFILL_START` | *(all history)* | First/full import starts at this date (`YYYY-MM-DD`) |
| `RECONCILE_MAX_REMOVAL_RATIO` | `0.2` | Full sync refuses to mark more than this share of rows Removed (override with `--force-reconcile`) |

## Trips & categories

| Variable | Default | Meaning |
|---|---|---|
| `TRIP_GROUP_PATTERN` | `^\s*(?:trip\|travel\|vacation)\s*[:\-–—]\s*(?P<name>.+?)\s*$` | Regex for trip group names; must contain `(?P<name>…)` |
| `TRIP_AUTO_CREATE` | `true` | Unknown `#hashtag` / `[bracket]` tags create a trip |
| `SUBCATEGORY_MIN_OCCURRENCES` | `3` | Repeats needed before a subcategory is suggested |
| `SUBCATEGORY_AUTO_APPROVE` | `false` | `true` = suggestions become Active immediately |

## Output

| Variable | Default | Meaning |
|---|---|---|
| `DASHBOARD_CHARTS` | `true` | Upload SVG charts to the dashboard |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `text` | `json` for structured logs (used in Actions) |

## Things you configure in Notion (no code)

| Where | What |
|---|---|
| TUF Categories → *Monthly Budget* | Per-category / per-subcategory budget |
| TUF Categories → *Classification Rules* | Comma-separated keywords, e.g. `blue bottle, philz` (beat built-in rules) |
| TUF Categories → *Status* | `Active`, `Suggested` (review), `Rejected` (never suggest again), `Archived` |
| TUF Categories → new row | New category: leave *Parent* empty for a primary category, or choose a parent for a subcategory |
| TUF Trips → *Budget*, *Start/End Date* | Trip budget and dates (dates auto-fill from expenses if empty) |
| TUF Trips → *Splitwise Groups* | Comma-separated group ids or exact names that belong to the trip |
| TUF Trips → *Tags* | Extra hashtags/aliases, e.g. `bigsur26, big sur` |
| TUF Expenses → *Category* / *Subcategory* | Edit to override; TUF locks the row automatically |
| TUF Expenses → *Category Locked* | Untick to let TUF re-categorize the row |

## CLI

```
tuf check                         # read-only validation of everything
tuf setup [--views]               # create databases + dashboard (idempotent)
tuf sync                          # incremental (first run imports history)
tuf sync --full                   # re-fetch everything and reconcile removals
tuf sync --since 2025-01-01       # backfill from a date
tuf sync --dry-run                # compute and print the plan, write nothing
tuf sync --no-ai                  # skip Gemini this run
tuf reclassify [--dry-run]        # re-run categorization on all unlocked rows
tuf report -o report.html         # private local HTML report from Notion
tuf demo -o demo/                 # fictional demo report (no credentials)
```

Exit codes: `0` ok · `1` sync finished with errors · `2` invalid configuration · `3`
connection/permission/workspace problem.
