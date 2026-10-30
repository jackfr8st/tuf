# External service research

**Verified on:** 2026-10-29, from official documentation (fetched live) unless marked otherwise.
Third-party services change their APIs, limits and pricing. Re-check before relying on any
number below. Nothing here is a promise that a service will stay free.

Status legend: **Verified** = read in official docs or observed directly · **Unverified** = not
stated in official docs; TUF is designed to work either way.

---

## 1. Splitwise API

Source: <https://dev.splitwise.com/> (single-page reference, including Terms of Use).

| Topic | Finding | Status |
|---|---|---|
| Base URL | `https://secure.splitwise.com/api/v3.0/` | Verified |
| Auth | Each endpoint accepts OAuth or API key (`Authorization: Bearer <key>`). A personal API key is issued when you register an app at <https://secure.splitwise.com/apps>. | Verified (auth types); the registration page is the standard flow |
| Identity | `GET get_current_user` returns `id`, names, `default_currency` | Verified |
| Expenses | `GET get_expenses` with `group_id`, `friend_id`, `dated_after/before`, `updated_after/before`, `limit` (default 20), `offset` (default 0). Without `group_id`/`friend_id` it returns expenses across all groups and non-group expenses. | Verified (params); the "all expenses" behaviour is the documented default when no filter is given |
| Max `limit` | Not documented | Unverified → TUF pages until an **empty** page, never stopping on a short page |
| Groups | `GET get_groups`; non-group expenses use pseudo-group id `0` | Verified |
| Expense fields | `cost` (decimal string), `currency_code`, `date`, `payment` (bool), `created_at/updated_at`, `deleted_at/deleted_by`, `repayments`, `users[]` with `user_id`, `paid_share`, `owed_share`, `net_balance` | Verified |
| Deleted expenses in listings | Not stated. Community clients report deleted expenses are returned with `deleted_at` set. | Unverified → TUF handles `deleted_at`, **and** a daily full sync marks rows that vanish as `Removed` |
| Rate limits | Limits exist, vary by endpoint, can change without notice; "fairly lenient" for hobby use; HTTP 429 when exceeded | Verified |
| Write endpoints | Some return HTTP 200 with an `errors` body | Verified → TUF checks `errors` on every response (it only reads) |
| Terms | "not intended for commercial use"; may not be used with any fee-based service; Splitwise may condition use on an active **Splitwise Pro** subscription; may revoke access at any time; integrations must obtain explicit consent and have a privacy policy; data may not be sold | Verified |

**Blockers / risks**
- Splitwise *may* require Pro for API access in the future. TUF cannot work around this and does
  not try to. If that happens, the only legitimate fallback is the Pro requirement itself or a
  manual export; TUF does not scrape.
- TUF is free, non-commercial, self-hosted software: each user registers their own Splitwise app
  and processes only their own data. `docs/PRIVACY.md` is the privacy policy for a TUF instance.
- Do not use Splitwise logos or imply endorsement (README states non-affiliation).

## 2. Notion API

Sources: <https://developers.notion.com/reference/versioning>,
<https://developers.notion.com/page/changelog>,
<https://developers.notion.com/guides/get-started/upgrade-guide-2025-09-03>,
<https://developers.notion.com/reference/request-limits>,
<https://developers.notion.com/reference/query-a-data-source>,
<https://developers.notion.com/reference/property-schema-object>,
<https://developers.notion.com/guides/data-apis/working-with-views>,
<https://developers.notion.com/guides/data-apis/uploading-small-files>,
<https://www.notion.com/help/charts>.

| Topic | Finding | Status |
|---|---|---|
| Version | `Notion-Version: 2026-03-11` (breaking: `archived`→`in_trash`, `after`→`position`, `transcription`→`meeting_notes`). TUF uses this version. | Verified |
| Data model | Since 2025-09-03 a database contains one or more **data sources**. Pages are created with `parent: {type: data_source_id}`; schemas are updated with `PATCH /v1/data_sources/{id}`; queries use `POST /v1/data_sources/{id}/query` with cursor pagination. `GET /v1/databases/{id}` lists `data_sources`. | Verified |
| Create database | `POST /v1/databases` with `initial_data_source.properties` | Verified |
| Relations | Request uses `data_source_id` with `single_property` or `dual_property` | Verified |
| Number formats / select colors | `dollar`, `euro`, `percent`, …; colors `default, gray, brown, orange, yellow, green, blue, purple, pink, red`; option names may not contain commas | Verified → TUF strips commas from select values |
| Rate limits | 180 requests/min per connection on non-Business plans (~3/s); data-source queries ~3/s; 429 with `Retry-After`; do not retry `public_api_request_blocked`; retry 5xx only for idempotent requests | Verified → TUF paces at ~3/s and follows these rules |
| Size limits | rich text 2,000 chars; 100 blocks per append; 100 relation items per request; 500 KB payload | Verified |
| Formulas / rollups | Formula expressions can be set via API (stored as written, Aug 2026). Formula/rollup values can return `unsupported` when they depend on many related pages. | Verified → TUF maintains aggregates itself (exact Decimal) instead of relying on rollups |
| Views API | `POST /v1/views` (Mar 2026). Types include `chart` (`column/bar/line/donut/number`), `gallery`, `dashboard`. Linked views on a page via `create_database`. | Verified (shape partly: chart `sort` sub-schema not fully read) |
| Free-plan charts | "If you're on a Free plan, you can create one chart" (Help Center). Whether API-created charts count toward that limit is not stated. | Verified (limit) / Unverified (API interaction) |
| File uploads | Direct upload: `POST /v1/file_uploads` then `POST /v1/file_uploads/{id}/send` (multipart field `file`); attach to an `image` block as `{type: file_upload}` within **1 hour**; Free workspaces: **5 MiB per file**; `image/png` and `image/svg+xml` are supported types | Verified |
| SVG rendering inside Notion image blocks | Supported type per docs; visual rendering not tested by us | Unverified |

**Implications:** Notion is TUF's only durable store (no paid database). The Free plan's
single native chart is not enough for the dashboard, so TUF renders its own SVG charts and
uploads them as images (small files, well under 5 MiB), plus native KPI callouts, gauges and
toggles. One optional native chart is created by `tuf setup --views`.

## 3. Google Gemini API (optional)

Sources: <https://ai.google.dev/gemini-api/docs/pricing>,
<https://ai.google.dev/gemini-api/docs/rate-limits>,
<https://ai.google.dev/gemini-api/docs/structured-output>; SDK inspected locally
(`google-genai` 2.34.0).

| Topic | Finding | Status |
|---|---|---|
| Free tier models | Pricing page lists "Free of charge" input/output for e.g. `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-flash-lite` | Verified (2026-10-29) |
| Default model | `gemini-3.1-flash-lite` (free tier, non-preview). Configurable via `GEMINI_MODEL`; `tuf check` verifies the model exists. | Decision |
| Free-tier data use | Free plan: "Content used to improve our products" | Verified → disclosed in PRIVACY.md; AI is opt-in |
| Rate limits | Not published per model; shown in AI Studio; "not guaranteed" | Verified → TUF caps requests per run and stops on 429 |
| Structured output | SDK: `client.models.generate_content(model, contents, config=GenerateContentConfig(system_instruction, response_mime_type="application/json", response_json_schema=...))`. Docs note `minLength`/`maxLength` constraints are stripped, so validate in code. | Verified (SDK signature by introspection; docs note) |
| Errors | `google.genai.errors.APIError` / `ClientError` with `.code` (429 for quota) | Verified (SDK introspection) |

## 4. GitHub Actions

Sources: <https://docs.github.com/en/billing/concepts/product-billing/github-actions>,
<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>.

| Topic | Finding | Status |
|---|---|---|
| Public repos | "The use of standard GitHub-hosted runners is free … in public repositories" | Verified |
| Private repos (GitHub Free) | $12/month included compute, expressed as money; Linux 2-core is $0.006/min → about 2,000 min (our arithmetic) | Verified (price), derived (minutes) |
| Overage | Without a payment method, usage is blocked when the quota is used up; budgets limit spend when a payment method exists | Verified → TUF docs tell users to keep a $0 budget |
| Per-job rounding | Not stated on the page | Unverified → we assume each run bills ≥ 1 minute |
| Schedule | Minimum interval 5 minutes; can be delayed under load; queued jobs may be dropped; schedules in public repos are disabled after 60 days without repository activity | Verified |
| Secrets | Repository secrets are not passed to workflows triggered from forks | Standard GitHub behaviour |

**Schedule decision:** a 15-minute schedule is ~2,900 runs/month → exceeds the private Free
allowance if each run bills ≥ 1 minute. Default is **hourly** (~720 runs + 30 daily full runs
≈ 750–1,500 billed minutes on a private repo; actual runtime not yet measured against live
APIs). Users who check once or twice a day can switch to `17 7,19 * * *`.

Privacy note: Actions logs of a **public** repository are publicly readable. TUF logs counts
only, but we still recommend a **private** repository.

## 5. Currency conversion (optional)

Source: <https://frankfurter.dev/>; endpoint verified with a live call on 2026-10-29:
`GET https://api.frankfurter.dev/v2/rate/eur/usd?date=2026-10-24` →
`{"date":"2026-10-24","base":"EUR","quote":"USD","rate":1.1218}`.

Free, no key, open source, blended central-bank reference rates (ECB among providers),
caching encouraged, self-hostable. TUF stores the rate **and the date returned** on every
converted row; if no rate is available, the row is marked `Needs FX` and excluded from
totals rather than guessed.
