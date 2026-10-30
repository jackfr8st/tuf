# Privacy

TUF is self-hosted software. There is no TUF server, no telemetry, and no TUF account. Each
person runs their own instance with their own credentials and processes **only their own
data**. This page describes what a TUF instance does with data. It serves as the privacy
policy that Splitwise's API terms require of an integration.

## Data flow

| From → To | What | When |
|---|---|---|
| Splitwise → TUF | Your user id and default currency; your groups (id and name); every expense you're listed on (description, cost, currency, date, category, group, and each participant's **user id** and paid/owed shares) | Every run (read-only) |
| TUF → your Notion | One row per expense you're part of, with **your** shares only, plus categories, trips, budgets and the dashboard | Every run |
| TUF → frankfurter.dev *(optional, on by default)* | A currency pair and a date, e.g. `EUR→USD on 2026-10-24`. No amounts, no descriptions | Only for expenses not in your reporting currency |
| TUF → Google Gemini *(optional, opt-in)* | See below | Only for expenses no rule could classify |
| TUF → GitHub Actions logs | Run counts, Splitwise expense **ids**, error messages. For source-data consistency warnings, the amounts involved | Every scheduled run |

TUF **never stores** other participants' names, emails, photos or balances. It keeps only
the numeric user ids needed to find your own share. TUF never writes to Splitwise.

## Gemini (optional AI)

AI categorization is **off unless you set `GEMINI_API_KEY`**. Set `AI_ENABLED=false` to turn
it off while keeping the key.

When enabled, each request contains only:

- the expense **description**, after TUF removes emails, URLs, phone/card-like numbers and
  `@mentions`, truncated to 160 characters,
- a coarse context hint: "trip group", "household group" or "shared group" (never the
  group's name or members),
- your category, subcategory and trip **names**, so the answer can be validated.

It **never** contains amounts, dates, currencies, ids, participants or group names. Only
expenses that your manual edits, rules, learned labels, the keyword catalog and Splitwise's
own category all failed to classify are sent. Results are cached on the row, so an unchanged
expense is never sent twice. Each run makes at most `AI_MAX_REQUESTS_PER_RUN` requests.

**Important:** on Gemini's free tier, Google states that content may be used to improve its
products (checked 2026-10-29). Don't enable AI if your expense descriptions contain
anything you wouldn't share with Google. On a paid Gemini plan the terms differ, but TUF
doesn't require one and never enables billing.

Descriptions are treated as untrusted data in the prompt. Model output is validated: unknown
categories, low-confidence answers and titles containing numbers that weren't in the
original are rejected. AI never computes or changes an amount.

## Where your data lives

- **Notion:** your workspace, under the page you shared. Visible to whoever can see that page.
- **GitHub:** only your credentials, as encrypted Actions secrets. TUF doesn't commit data
  to the repository. Actions **logs of a public repository are public**, so use a **private**
  repository.
- **Your computer:** `tuf report` and `tuf demo` write HTML files you choose. `tuf-report*.html`
  is git-ignored. Treat reports as private financial documents.
- **Temporary lock file:** `tuf-sync.lock` in the system temp folder. It holds only a
  process id and is removed when the run ends.

## Deleting your data

1. **Stop syncing:** disable the *TUF sync* workflow (Actions → TUF sync → ••• → Disable),
   or delete the repository.
2. **Delete synced data:** delete the root page you shared (it contains every TUF database),
   then empty it from Notion's trash.
3. **Revoke access:**
   - Splitwise: <https://secure.splitwise.com/apps>, delete your registered app.
   - Notion: <https://www.notion.so/profile/integrations>, delete the integration.
   - Gemini: <https://aistudio.google.com/apikey>, delete the key.
   - GitHub: Settings → Secrets and variables → Actions, delete the secrets.
4. **Local copies:** delete `.env` and any generated reports.

Data already sent to Gemini is subject to Google's retention policy. TUF can't recall it.

## Rotating credentials

If a key might have leaked (pasted somewhere, committed, shared in a screenshot):

1. Create a new key at the provider (links above) and **delete the old one**.
2. Update the GitHub secret (or `.env`) with the new value.
3. Run `tuf check` or the workflow to confirm.

Splitwise API keys act as your account, so rotate them immediately if exposed. To report a
vulnerability in TUF itself, see [SECURITY.md](../SECURITY.md).
