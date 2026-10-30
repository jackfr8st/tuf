# Implementation status and audit

**Last updated:** 2026-10-29 · **Version:** 0.1.0

**Verdict:** TUF is **feature-complete and verified offline**. The financial engine,
categorization, trips, analytics, sync algorithm, dashboard rendering, config and CLI are
covered by automated tests against faithful fakes of the Splitwise, Notion and Gemini APIs.
**No live integration has been run yet**, because that needs your credentials. Until the
steps in [What still needs you](#what-still-needs-you) pass, treat TUF as a release
candidate, not production-proven.

## Verification results

| Check | Command | Result (2026-10-29) |
|---|---|---|
| Unit and integration tests (offline) | `uv run pytest` | **122 passed**, 2 skipped (live tests, need credentials) |
| Lint | `uv run ruff check src tests` | pass |
| Format | `uv run ruff format --check src tests` | pass |
| Types | `uv run mypy` (strict) | pass, 39 source files |
| Dependency audit | `uv run pip-audit --skip-editable` | no known vulnerabilities |
| Clean install | `uv build`, then install the wheel into a fresh Python 3.11 venv | `tuf --version` ok; `tuf check` with no config → exit 2 with actionable messages; `tuf demo` ok |
| Secret scan | regex scan for Notion/Gemini/GitHub/OpenAI-style tokens, personal names, emails | none (only fake values in test fixtures) |
| Demo assets | `tuf demo` regenerated and compared with `docs/images/` | identical |
| CI on GitHub (3.11–3.13) | `.github/workflows/ci.yml` | **not yet run** (nothing has been pushed) |
| Live Splitwise / Notion / Gemini | `TUF_LIVE_TESTS=1 uv run pytest tests/live` | **not run** (no credentials) |

Status legend: **PASS** = behaviour verified by an automated test or direct observation ·
**PARTIAL** = verified with limitations noted · **UNVERIFIED** = implemented but needs live
services or your account to prove · **FAIL** = not met.

## Requirements traceability

Tests live under `tests/`; `fin` = `unit/test_finance.py`, `cat` = `unit/test_categorization.py`,
`trip` = `unit/test_trips.py`, `ana` = `unit/test_analytics.py`, `plat` = `unit/test_platform.py`,
`swc` = `unit/test_splitwise_client.py`, `sync` = `integration/test_sync.py`.

### Splitwise integration and financial correctness (Parts 2.1, 5)

| ID | Requirement | Location | Tests | Status | Notes |
|---|---|---|---|---|---|
| F-1 | Identify the user by id, never by name | `splitwise/client.py` | swc `test_current_user_uses_id_not_name` | PASS | |
| F-2 | All groups, non-group expenses, exhaustive pagination | `splitwise/client.py` | swc `test_pagination_continues_past_short_pages_until_empty`, `test_duplicates_across_pages_keep_latest_version`; sync `test_pagination_imports_everything` | PASS (offline) | Max page size isn't documented by Splitwise. TUF pages until an empty page |
| F-3 | Personal share = `owed_share`; spending excludes payments for others | `finance/engine.py` | fin: 14 scenario tests (full payer, other payer, unequal, personal, zero owed/paid, multi-party cents) | PASS | |
| F-4 | Settlements never counted as spending | `finance/engine.py` | fin `test_settlement_paid_…`, `test_settlement_received_…` | PASS | |
| F-5 | Refunds and reimbursements | `finance/engine.py` | fin `test_refund_…`, `test_reimbursement_…` | PASS | |
| F-6 | Exact decimal arithmetic | `money.py` | fin `test_decimal_precision_no_float_drift`, `test_money_helpers` | PASS | |
| F-7 | Missing user allocation never fabricated | `finance/engine.py` | fin `test_missing_user_allocation_returns_none_never_fabricates` | PASS | |
| F-8 | Inconsistent source data flagged, not altered | `finance/engine.py` | fin `test_inconsistent_source_data_is_flagged_not_altered` | PASS | |
| F-9 | Original currency preserved; conversion records rate and date; no guessing | `finance/fx.py` | fin `test_foreign_currency_…`, `test_no_rate_…`; sync `test_foreign_currency_converted_or_flagged` | PASS (offline) | Frankfurter endpoint shape verified by one live call during research |
| F-10 | Malformed input rejected and isolated | `splitwise/parse.py` | fin `test_parse_rejects_bad_data`; swc `test_incremental_params_and_parse_errors_isolated` | PASS | |
| F-11 | Bounded retries, `Retry-After`, no retry on auth errors | `http.py` | swc `test_retries_on_429_…`, `test_retry_is_bounded`, `test_auth_failure_is_not_retried`, `test_post_not_retried_on_500_…`, `test_errors_in_200_body_raise` | PASS | |
| F-12 | Live Splitwise compatibility | `splitwise/` | `tests/live` | UNVERIFIED | Needs your API key |

### Categorization (Part 6)

| ID | Requirement | Location | Tests | Status | Notes |
|---|---|---|---|---|---|
| C-1 | Nine baseline categories with subcategories | `categorization/taxonomy.py` | plat `test_taxonomy_defaults_cover_spec_categories` | PASS | |
| C-2 | Deterministic pipeline: manual → user rules → learned → keywords → Splitwise category → AI → Other | `categorization/engine.py` | cat `test_known_merchants_…`, `test_user_rules_beat_…`, `test_learned_from_locked_rows`, `test_splitwise_category_used_…`, `test_unknown_merchant_without_ai_…`, `test_word_boundaries_…` | PASS | |
| C-3 | Manual edits preserved | `categorization/engine.py`, `sync/engine.py` | cat `test_manual_override_is_preserved`; sync `test_manual_category_edit_is_preserved_and_locked` | PASS | |
| C-4 | Gemini structured output, validated | `categorization/ai.py` | plat `test_gemini_adapter_structured_output_request`; cat `test_ai_malformed_json_…`, `test_ai_invalid_category_and_low_confidence_…` | PASS (adapter, against SDK signatures) | |
| C-5 | AI failure, quota and outage fall back safely | `categorization/` | cat `test_ai_unavailable_…`, `test_ai_quota_exceeded_…`; plat `test_gemini_adapter_errors_map_to_fallbacks`; sync `test_ai_quota_then_retry_on_next_run` | PASS | |
| C-6 | AI efficiency: cache, batch, per-run cap, only unresolved | `categorization/engine.py` | cat `test_ai_cache_prevents_repeat_calls_…`, `test_ai_request_budget_is_bounded` | PASS | |
| C-7 | Prompt-injection resistance; no amounts or ids sent | `categorization/ai.py`, `text.py` | cat `test_prompt_injection_…`, `test_ai_prompt_contains_no_amounts_or_ids` | PASS | |
| C-8 | Dynamic subcategories: suggest after N repeats, approve, promote | `categorization/suggestions.py`, `sync/engine.py` | cat `test_repeated_merchant_…`, `test_suggestions_skip_…`, `test_promotion_…`, `test_latent_subcategory_…`; sync `test_recurring_pattern_suggests_subcategory_and_approval_promotes` | PASS | |
| C-9 | Live Gemini classification quality | `categorization/ai.py` | none | UNVERIFIED | Needs a key; quality on real descriptions is unmeasured |

### Trips (Part 7)

| ID | Requirement | Location | Tests | Status | Notes |
|---|---|---|---|---|---|
| T-1 | Detection by group mapping, group name, `#hashtag`, `[bracket]`, AI hint (existing trips only) | `trips/engine.py` | trip (all 10 tests); cat `test_ai_trip_hint_only_for_known_trips` | PASS | |
| T-2 | No double counting (one relation per expense, no copies) | `trips/`, `analytics/engine.py` | ana `test_trip_totals_reconcile_without_double_counting`; sync `test_trip_hashtag_creates_trip_and_links_once` | PASS | |
| T-3 | Similar trip names don't collide | `trips/engine.py` | trip `test_multiple_and_similar_trip_names_do_not_collide` | PASS | |
| T-4 | Trip analytics (total, budget, top category, dates) | `analytics/engine.py`, `notion/store.py` | ana `test_trip_totals_…`; sync `test_trip_group_name_pattern` | PASS | |

### Notion database and dashboard (Parts 8, 9, 10)

| ID | Requirement | Location | Tests | Status | Notes |
|---|---|---|---|---|---|
| N-1 | Expenses, Categories, Trips, Budgets schemas created and validated | `notion/schema.py`, `store.py` | sync `test_unset_workspace_…`, `test_schema_drift_…`, `test_schema_wrong_type_blocks_sync` | PASS (against fake) | Request shapes taken from Notion docs (API 2026-03-11) |
| N-2 | Totals reconcile (month, category, trip, budget %) | `analytics/engine.py` | ana `test_monthly_and_category_totals_reconcile`, `test_budget_percentages_and_projection`, `test_subcategory_budget`; plat `test_demo_reconciles` | PASS | |
| N-3 | Deleted, removed, settlement and unconverted rows excluded | `analytics/engine.py` | ana `test_archived_settlements_and_unconverted_are_excluded` | PASS | |
| N-4 | Budget gauges and thresholds | `analytics/engine.py` | ana `test_budget_status_thresholds_and_gauge`, `test_no_budget_…`; sync `test_budget_rows_and_category_aggregates` | PASS | |
| N-5 | Forecast uses valid inputs; month-over-month compares the same period | `analytics/engine.py` | ana `test_forecast_inputs_valid_on_first_day`, `test_month_over_month_uses_same_period` | PASS | Projection is labelled as an estimate |
| N-6 | Insights: spikes, recurring merchants, net balance | `analytics/insights.py` | ana `test_spike_detection_…`, `test_recurring_…`, `test_net_balance_…` | PASS | |
| N-7 | Dashboard sections A–F, user content above the marker preserved, rewritten only on change | `notion/dashboard.py` | plat `test_dashboard_blocks_and_hash_stability`; sync `test_dashboard_rendered_once_per_content_change` | PASS (against fake) | |
| N-8 | Charts (SVG, no scripts, accessible) | `analytics/charts.py` | plat `test_charts_are_valid_svg_and_contain_no_scripts`; visually reviewed in the demo report | PARTIAL | **Rendering of uploaded SVGs inside Notion is unverified** |
| N-9 | Native views (one chart on Free plan) | `notion/views.py` | none | UNVERIFIED | Views API is new; best-effort with documented manual fallback |
| N-10 | Live Notion compatibility | `notion/` | `tests/live` | UNVERIFIED | Needs your integration and page |

### Synchronization (Part 11)

| ID | Requirement | Location | Tests | Status | Notes |
|---|---|---|---|---|---|
| S-1 | Initial import and idempotent re-runs | `sync/engine.py` | sync `test_first_import_then_repeated_sync_is_idempotent` | PASS | |
| S-2 | Incremental with overlap; missed schedules self-heal | `sync/engine.py` | sync `test_missed_schedules_widen_the_window_automatically` | PASS | |
| S-3 | Updates recomputed | `sync/engine.py` | sync `test_updated_expense_is_recomputed`; fin `test_edited_transaction_…` | PASS | |
| S-4 | Deletions (soft, restorable) and reconciliation of vanished rows | `sync/engine.py` | sync `test_deleted_expense_soft_deleted_and_restorable`, `test_full_reconcile_…`, `test_reconcile_refuses_mass_removal` | PASS | Whether Splitwise lists deleted expenses is undocumented; the daily full sync covers both cases |
| S-5 | Failure safety: no writes after a Splitwise failure; transient Notion errors resume without duplicates | `sync/engine.py` | sync `test_splitwise_failure_aborts_…`, `test_transient_notion_failure_then_recovery_…`, `test_permanent_row_error_…` | PASS | |
| S-6 | Concurrency: lock and duplicate healing | `sync/lock.py`, `sync/engine.py` | plat `test_run_lock_…`; sync `test_concurrent_run_duplicates_are_healed` | PASS | Actions also uses a concurrency group |
| S-7 | Backfill, dry run | `sync/engine.py`, `cli.py` | sync `test_historical_backfill_since_date`, `test_dry_run_writes_nothing` | PASS | |
| S-8 | Durable state without a database (derived from Notion) | `sync/engine.py` | sync `test_missed_schedules_…` | PASS | |

### Automation, configuration, security, distribution (Parts 12–17)

| ID | Requirement | Location | Tests / evidence | Status | Notes |
|---|---|---|---|---|---|
| A-1 | Scheduled and manual runs, configurable frequency, concurrency, minimal permissions, caching | `.github/workflows/sync.yml` | Reviewed; YAML not yet executed on GitHub | UNVERIFIED | Needs a push and secrets |
| A-2 | CI: lint, format, types, tests, audit | `.github/workflows/ci.yml` | The same commands pass locally | PARTIAL | Not yet run on GitHub |
| A-3 | Cost: fits free allowances | RESEARCH.md §4 | Derived from published pricing | PARTIAL | **Actual run time not measured**; hourly ≈ 750–1,500 billed min/month on a private repo (estimate) |
| K-1 | Environment-driven config with all errors reported at once | `config.py` | plat `test_config_…` (3 tests); clean-install check | PASS | |
| K-2 | Validation command (`tuf check`) | `cli.py` | plat `test_cli_missing_config_exits_2` | PARTIAL | Connection checks need live services |
| P-1 | Secret-safe logging | `logging_utils.py` | plat `test_secrets_are_redacted_from_logs` | PASS | |
| P-2 | Minimal data sharing; privacy documentation | `text.py`, PRIVACY.md | cat `test_ai_prompt_contains_no_amounts_or_ids` | PASS | |
| P-3 | No secrets or real data in the repository | whole repo | Secret scan; demo data is generated and fictional | PASS | |
| D-1 | README, quick start, `.env.example`, lockfile, Notion setup, config reference, troubleshooting, privacy, security, contributing, license, sample data, demo visuals | repo root, `docs/`, `examples/` | Files present; docs cross-checked against code | PASS | |
| D-2 | Onboarding without editing Python | README quick start | Setup works entirely through GitHub UI and Notion | UNVERIFIED | Needs a real walk-through with fresh accounts |
| D-3 | Not implying Splitwise endorsement | README, CLI `--help` | Non-affiliation statement; no logos | PASS | |

## What still needs you

These steps need your credentials, accounts or approval. None has been done.

1. **Create credentials:** a Splitwise API key, a Notion internal integration plus a shared
   root page, and optionally a Gemini key.
2. **Live read-only check:** put them in `.env`, then run `uv run tuf check` and
   `TUF_LIVE_TESTS=1 uv run pytest tests/live`.
3. **First live write (needs your approval):** `uv run tuf setup --views`, then
   `uv run tuf sync --dry-run`, then `uv run tuf sync`. Afterwards, compare one month's
   total against Splitwise and check that the dashboard's SVG charts render (N-8, N-9).
4. **GitHub (needs your approval):** commit, push to a **private** repository, add secrets,
   and run *TUF sync* in **setup** mode. Note the run time to confirm the cost estimate (A-3).

## Known limitations

- **Removals arrive within a day.** An expense you're removed from, or one that Splitwise
  stops listing, becomes *Removed* on the daily full sync, not the hourly run.
- **Large first imports may take more than one run.** Notion allows about 3 requests per
  second, so thousands of expenses can exceed the 20-minute job limit. Writes happen in
  `updated_at` order, so the next run continues where the last one stopped. You can also
  run the first import locally, or set `BACKFILL_START`.
- **One trip per expense** (by design, to prevent double counting).
- **Gemini free tier** may use inputs to improve Google's products, and its limits aren't
  guaranteed. AI is opt-in.
- **Splitwise terms** allow Splitwise to restrict API access (for example, to Pro users).
  TUF won't work around that.
- **Notion Free plan:** only one native chart, so the dashboard charts are uploaded SVG
  images. Notion's built-in formulas and rollups aren't used for totals, because they can't
  exclude settlements or deleted rows.
- **Exchange rates** are daily reference rates (Frankfurter), not the rate your card used.

## Technical decisions

See [ARCHITECTURE.md § Key decisions](ARCHITECTURE.md#key-decisions) and
[RESEARCH.md](RESEARCH.md) for the evidence behind each one.

## Next steps

1. Run the live verification above and record the results here.
2. Measure Actions run time and update the cost estimate.
3. Decide whether to publish the repository publicly (it contains no personal data).
