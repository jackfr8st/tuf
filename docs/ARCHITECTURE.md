# Architecture

TUF is a stateless batch job. Each run reads Splitwise, computes everything deterministically,
writes only what changed to Notion, and exits. Notion is the only durable store.

```
Splitwise API ──► splitwise/  (identity, groups, paginated expenses)
                     │
                     ▼
                 finance/     (personal share, transaction type, FX)        ◄─ exact Decimal
                     │
                     ▼
              categorization/ (manual → rules → learned → keywords → Splitwise → AI → Other)
                     │
                     ▼
                  trips/      (group mapping → group name → #hashtag → [bracket] → AI hint)
                     │
                     ▼
                  sync/       (diff vs Notion rows, ordered writes, reconcile, de-duplicate)
                     │
                     ▼
                 notion/      (store, schema migration, dashboard blocks, chart uploads)
                     │
                     ▼
                analytics/    (month KPIs, budgets, trends, trips, recurring, insights, SVG)
```

## Modules

| Path | Responsibility |
|---|---|
| `src/tuf/money.py` | Decimal parsing/rounding/formatting; float only at the JSON boundary |
| `src/tuf/models.py` | Domain types: `SplitwiseExpense`, `FinancialFacts`, `LedgerEntry`, enums |
| `src/tuf/http.py` | Retrying HTTP client: bounded attempts, `Retry-After`, idempotency-aware |
| `src/tuf/splitwise/` | Parse + fetch. Pages until an empty page; pins `updated_before` to run start |
| `src/tuf/finance/engine.py` | `compute_facts()`: the accounting rules below |
| `src/tuf/finance/fx.py` | Optional Frankfurter conversion; stores rate + date |
| `src/tuf/categorization/` | Taxonomy, keyword catalog, Gemini adapter, suggestions |
| `src/tuf/trips/engine.py` | Trip matching by explicit identifiers |
| `src/tuf/notion/` | REST client (2026-03-11), schema, mapping, store, dashboard, views |
| `src/tuf/analytics/` | Dashboard numbers, insights, SVG charts |
| `src/tuf/sync/engine.py` | Orchestrator; `make_entry()` is the single place rows are built |
| `src/tuf/report/html.py` | Local HTML report |
| `src/tuf/cli.py` | `check`, `setup`, `sync`, `reclassify`, `report`, `demo` |

Business logic (finance, categorization, trips, analytics) has no dependency on HTTP or SDKs.

## Financial invariants

For the authenticated user (identified by **id** from `get_current_user`, never by name):

| Case | Type | My Share / Spending |
|---|---|---|
| `payment=false`, `owed_share > 0` | Expense | `owed_share` |
| `payment=false`, `owed_share < 0` | Refund | `owed_share` (negative, reduces totals) |
| `payment=false`, `owed_share = 0`, `paid_share > 0` | Paid for Others | 0 |
| `payment=false`, `owed_share = 0`, `paid_share < 0` | Reimbursement | 0 |
| `payment=true`, `paid − owed > 0` | Settlement Paid | 0 |
| `payment=true`, `paid − owed < 0` | Settlement Received | 0 |
| listed with zero shares | No Share | 0 |
| user not listed | *(no row; existing row → Removed)* | — |
| `deleted_at` set | status Deleted | 0 (raw shares kept for audit) |

Always: `net_contribution = paid_share − owed_share` (never `paid + owed`).
`Spending` (reporting currency) = My Share converted with a stored rate, `null` when no rate
exists (status `Needs FX`, excluded from totals). Every chart and total sums `Spending` over
**Active** rows only, so settlements, deletions, removals and duplicates never inflate it.
Source data that violates `Σpaid = Σowed = cost` is flagged as a warning, never "fixed".

## Sync algorithm

1. Resolve identity and groups (Splitwise); discover the workspace (Notion root page children).
2. Validate the schema; add missing properties (never delete or retype).
3. Load Categories, Trips, and all Expenses rows. Index by `Sync Key = sw:<account>:<expense>`;
   extra rows with the same key are duplicates.
4. **Window.** First run, `--full`, `--since`: full history (or from `BACKFILL_START`).
   Otherwise `updated_after = max(Source Updated in Notion) − SYNC_OVERLAP_HOURS`. No local
   state file: a missed or late schedule simply widens the next window.
5. Compute facts → classify (AI only for unresolved, uncached, non-deleted) → match trip →
   `make_entry()`.
6. Diff against the stored row (`comparable()`); unchanged rows are not written.
7. Write in ascending `updated_at`. A transient error (429 after retries, 5xx, network) stops
   further writes, so the derived checkpoint never moves past an unwritten row. Permanent
   (4xx) errors are reported and skipped.
8. Full runs: rows whose expense is no longer returned become `Removed` (guarded: refuses if
   more than `RECONCILE_MAX_REMOVAL_RATIO` of rows would be removed).
9. Trash duplicate rows (keeps locked / oldest).
10. Dynamic subcategories: create suggestions; promote rows once a subcategory is Active.
11. Analytics → maintained aggregates (Categories, Trips, Budgets; only changed values) →
    dashboard (only if its content hash changed).

## Classification

`Category Locked` (or any manual edit of Category/Subcategory, detected by an output hash in
`Class Key`) freezes a row's labels. Locked rows also teach TUF: an identical future
description (same fingerprint) gets the same labels (`Learned`). AI results are cached on the
row with the input hash; unchanged expenses are never re-sent. AI output is validated
against the live category list; invalid categories, low confidence, or titles containing
numbers not present in the original description are rejected.

## Key decisions

| Decision | Why |
|---|---|
| Plain `httpx` instead of Notion/Splitwise SDKs | Full control of retries and pacing; trivially mockable; fewer dependencies |
| Aggregates maintained by TUF, not rollups | Rollups cannot exclude settlements/deleted rows; `unsupported` on large relations |
| Soft deletion (Status) instead of trashing | Reversible; keeps audit trail; trash would also break relations |
| Subcategories as selects + Categories DB for metadata | Selects chart/group natively; DB holds budgets, rules, approval status |
| SVG charts uploaded as images | Free plan allows one native chart; SVG is tiny and dependency-free |
| Hourly default schedule | Fits private-repo free allowance; 15-minute does not (see RESEARCH.md) |
| One trip per expense (relation) | Guarantees no double counting across trips |
