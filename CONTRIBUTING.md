# Contributing

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt   # fast, test-only deps (sqlite)
# or: pip install -r requirements-dev.txt   # full runtime + test toolchain
```

## Running the tests

The suite runs on an in-memory sqlite via `sopds/settings_test.py`:

```bash
pytest
pytest --cov=opds_catalog --cov=sopds_web_backend --cov=book_tools --cov-report=term-missing
```

CI runs the same suite on Python 3.12 for every pull request, plus a
`ruff` lint pass (blocking).

## Linting / type-checking

Config lives in `pyproject.toml` (tool config only; deps stay in
`requirements*.txt`).

```bash
ruff check .          # syntax errors + pyflakes (E9, F)
mypy opds_catalog sopds_web_backend book_tools   # lenient; tighten over time
```

`ruff check .` is enforced in CI (rules `E9`, `F`, excluding the noisy `F841`).
Keep it clean.

## Conventions

- **Branch** from `master`; open a pull request against `master`.
- **Sign off** every commit (Developer Certificate of Origin):
  `git commit -s`.
- Keep commits focused; write a clear subject and a body explaining *why*.
- Add or update tests for behavioural changes. A missing-object lookup should
  return **404, not 500** (see the `get_object_or_404` usages and the
  `test_*_404.py` tests for the pattern).
- Note user-facing changes in `CHANGELOG.md` under **Unreleased**.

## Deployment

Production is deployed from the separate `sopds-k8s` repository (werf + GitLab
CI). That repo pins a specific `duckhawk/sopds` commit; after merging to
`master`, bump the pin there.
