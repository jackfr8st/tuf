# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's private
vulnerability reporting instead: the repository's **Security** tab, then **Report a
vulnerability**. Include steps to reproduce, and never include real credentials or real
financial data. We aim to acknowledge reports within 7 days. This is a volunteer project
with no guaranteed response time.

In scope: credential leakage (logs, errors, reports), data sent to a service it shouldn't
reach, prompt injection that changes amounts or escapes validation, workflow injection,
and anything that corrupts or silently discards financial records.

## If your credentials leak

Rotate them right away. Steps are in [docs/PRIVACY.md](docs/PRIVACY.md#rotating-credentials).

## How TUF protects you

- **Credentials** come only from environment variables or GitHub Secrets. `.env` is
  git-ignored, and secret values are redacted from every log line.
- **Least privilege:** workflows run with `permissions: contents: read` and
  `persist-credentials: false`. The Notion integration sees only the page you share. TUF
  never writes to Splitwise.
- **Workflow inputs** reach the shell only through environment variables, and `since` is
  validated against a strict date pattern.
- **Input validation:** Splitwise responses are parsed into typed models (malformed
  expenses are reported, never guessed). Configuration is validated before any network
  call.
- **AI output** is schema-validated and checked against your live category list. Expense
  descriptions are treated as untrusted data, and AI never produces or changes amounts.
- **Dependencies** are pinned in `uv.lock` and checked with `pip-audit` in CI.
- **Run summaries** in the Actions UI contain counts only.

## Supported versions

Only the latest release on `main` receives fixes.
