# Contributing

Thanks for helping. TUF handles people's money data, so correctness and privacy come
before features.

## Development setup

```bash
uv sync --all-extras          # Python 3.11+, https://docs.astral.sh/uv/
uv run pytest                 # offline; needs no credentials
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy                   # strict
uv run tuf demo -o tuf-demo   # fictional report to eyeball UI changes
```

CI runs all of the above on Python 3.11–3.13, plus `pip-audit`.

## Ground rules

1. **Money is `Decimal`.** Never use `float` for amounts except at the JSON boundary
   (`money.py`). Round only for display.
2. **Every financial change needs a test** that states the expected numbers exactly. The
   accounting rules in `docs/ARCHITECTURE.md#financial-invariants` are the contract.
   Changing one is a breaking change.
3. **AI never touches amounts.** It may only suggest labels, which are validated.
4. **No real data.** Fixtures, screenshots and issues use fictional data only (see
   `tuf/demo.py` and `tests/factories.py`). Never paste real descriptions, names or amounts.
5. **Keep business logic pure.** `finance/`, `categorization/`, `trips/` and `analytics/` don't
   do I/O. Network code goes in the clients, and tests use the fakes in `tests/fakes.py`.
6. **Respect provider terms and limits.** No scraping, no undocumented endpoints, and no
   new paid dependency.
7. **Mocks aren't proof.** If a change depends on live API behaviour you couldn't test, say
   so in the PR and update `docs/IMPLEMENTATION_STATUS.md`.

## Live tests

Tests marked `@pytest.mark.live` need real credentials and are skipped unless
`TUF_LIVE_TESTS=1`. Point them only at a throwaway Notion page.

## Pull requests

Keep PRs focused. Update the docs when behaviour or configuration changes, and add a line
to `docs/IMPLEMENTATION_STATUS.md` for new requirements.
