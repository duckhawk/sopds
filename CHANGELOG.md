# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- htmx live-search suggestions in the header search box (title/author/series),
  as progressive enhancement.
- pytest / pytest-django test suite with coverage; GitHub Actions CI running
  the suite on Python 3.12, plus a non-blocking `ruff` lint pass.
- Tooling config in `pyproject.toml` (ruff, mypy); `CONTRIBUTING.md`.

### Changed
- Upgraded to Django 5.2 LTS (Python 3.13 / PostgreSQL supported).
- Test dependencies are constrained by `requirements.txt` (`requirements-test.txt`
  uses `-c`), so the test environment never drifts from production.

### Fixed
- **Security:** open redirect in `LoginView` (`?next=` is now validated with
  `url_has_allowed_host_and_scheme`).
- Hardened the book-conversion route: `convert_type` restricted to `epub|mobi`
  and `ConvertFB2` raises `Http404` for anything else (was a 500).
- Return **404 instead of 500** for missing or non-numeric ids across the
  download/convert/read/cover views, the genre and "doubles" search views, and
  the corresponding OPDS feeds.

### Security
- Regression test locking in that `alphabet_menu()` passes user input as bound
  parameters (no SQL injection) and escapes LIKE wildcards.
