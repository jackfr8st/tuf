# Troubleshooting

**Start with `tuf check`.** It's read-only. It validates every setting and tests each
connection, and it reports every problem in one go. In GitHub Actions, run the workflow
manually with **dry run** ticked to see what a sync would do without writing anything.

Exit codes: `0` ok · `1` sync finished with errors · `2` invalid configuration · `3`
connection, permission or workspace problem.

## Configuration (exit code 2)

| Message | Fix |
|---|---|
| `SPLITWISE_API_KEY is missing` (or `NOTION_…`) | Add the secret. Locally: copy `.env.example` to `.env` and fill it in. In Actions: Settings → Secrets and variables → Actions → **Secrets** (not Variables) |
| `NOTION_ROOT_PAGE_ID is not a valid Notion page id or URL` | Paste the page's full URL (*Share → Copy link*) or its 32-character id |
| `TIMEZONE '…' is not a valid IANA zone` | Use a name like `Europe/London`, not `GMT+1` or `PST` |
| `… must be a whole number / a number` | Remove currency symbols and thousands separators: `MONTHLY_BUDGET=1500` |
| `TRIP_GROUP_PATTERN must contain a (?P<name>...) group` | Keep the named group. The default is in CONFIGURATION.md |

## Connections (exit code 3)

| Symptom | Cause and fix |
|---|---|
| Splitwise `401` / *authentication failed* | The key is wrong or was revoked. Copy the **API key** from <https://secure.splitwise.com/apps> (not the consumer key or secret) |
| Notion `401` | The integration secret is wrong or the integration was deleted |
| Notion `403`/`404` on the root page | The page isn't shared with the integration. On the page: **•••**, *Connections*, add it. Pages *inside* a shared page are included automatically |
| `TUF workspace … missing … Run tuf setup first` | Run `tuf setup`, or the workflow with mode **setup**. If you deleted a TUF database, setup recreates it **empty**. Restore it from Notion's trash instead to keep its data |
| `Notion schema is incompatible` | A property TUF needs was renamed, deleted or changed to another type. Rename it back, or restore it from page history. `tuf check` lists each one |
| `Gemini unavailable` | Bad key, a model that's unavailable to your key, or quota exhausted. TUF still works without AI. Set `AI_ENABLED=false` to silence it, or change `GEMINI_MODEL` |

## Sync results (exit code 1)

| Symptom | What's happening |
|---|---|
| `ABORTED: stopped after a transient error` | A service was rate-limited or down. TUF stops writing so it never skips a row. The next run resumes from where data in Notion ends. Nothing to do unless it repeats for hours |
| `reconciliation would remove N of M rows … skipped` | A full sync saw many expenses disappear. That's usually a temporary Splitwise problem, so TUF refuses to mark them *Removed*. If it's real (you left a big group), run `tuf sync --full --force-reconcile` |
| `WRITE ERROR: sw:…` | Notion rejected one row. The rest continue. Usually a property was edited into an incompatible shape; `tuf check` will say |
| `PARSE ERROR: expense …` | Splitwise returned a malformed expense. It's skipped, not dropped: an existing Notion row is kept (never marked *Removed*), and the expense is retried whenever it's fetched again (within the overlap window, or on the daily full sync) |
| `WARNING: expense …: sum(owed_share) … != cost …` | Splitwise's own numbers for that expense don't add up. TUF reports it but uses your share as-is and doesn't "fix" it |
| `another TUF sync appears to be running` | Two local runs overlapped. Wait, or delete `tuf-sync.lock` from your temp folder if no run is active (it expires after 2 hours anyway). In Actions, the workflow's concurrency group queues runs instead |

## Numbers look wrong

| Symptom | Explanation |
|---|---|
| Total differs from Splitwise's "total paid" | TUF counts what you **owe** for each expense (your share), not what you paid. Money you fronted for others shows as *Paid for Others* and isn't spending |
| An expense is missing from totals | Check its **Status** (only *Active* counts) and **Type**: settlements, *Paid for Others* and *No Share* are 0 by design |
| *Needs FX* rows | No exchange rate was available, so the row is excluded instead of guessed. It's retried whenever the expense is fetched again (daily full sync at the latest). Make sure `FX_ENABLED` isn't `false` |
| My own Notion chart shows a different total | Your view must sum **Spending** and filter **Status = Active** |
| Category changed back after I edited it | It shouldn't: an edit locks the row. If **Category Locked** is unticked, TUF may re-categorize it. Tick it |
| Deleted Splitwise expense still counted | Deletions arrive on the next incremental run (if Splitwise reports them) or on the daily full sync (it's then marked *Removed*). Check **Status** after the next full sync |

## GitHub Actions

| Symptom | Fix |
|---|---|
| Scheduled runs are late or skipped | GitHub doesn't guarantee schedule timing, especially at busy times. TUF catches up on the next run because each run re-checks recent history |
| Schedules stopped entirely | GitHub disables schedules in public repositories after 60 days without activity. Re-enable the workflow in the Actions tab, or make any commit |
| *Usage limit* / runs blocked | A private repo used up its free minutes. Switch to a less frequent cron in `.github/workflows/sync.yml`, e.g. `17 7,19 * * *` (twice a day) |
| Secrets empty in a forked repository | GitHub doesn't pass secrets to workflows triggered from forks. Use your own repository |

## Dashboard

| Symptom | Fix |
|---|---|
| Charts don't appear | Set `LOG_LEVEL=DEBUG` and look for `chart upload failed`. Charts are optional (`DASHBOARD_CHARTS=false`); the numbers are also shown as text |
| My notes on the dashboard vanished | Content below the *"Generated by TUF"* marker is regenerated. Keep your content above it |
| Dashboard didn't change | It's rewritten only when its numbers (or the date) change. Force it with `tuf sync --force-dashboard` |

Still stuck? Open an issue with the output of `tuf check` and the run summary. Remove any
amounts or descriptions you consider private first.
