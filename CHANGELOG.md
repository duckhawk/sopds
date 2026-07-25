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
  download/convert/read/cover views, the genre, "doubles" and search-by-id
  (`searchtype=i`) search views, and the corresponding OPDS feeds.
- Paginator no longer renders a spurious empty last page when the item count
  is an exact multiple of the page size, and clamps `SOPDS_MAXITEMS` to a
  minimum of 1 so a `0` value can no longer make every page 500.
- OPDS Basic-auth now answers **401** (not 500) for a malformed `Authorization`
  header (bad base64, non-utf8 bytes, or a missing `:` separator).
- MOBI parser keeps its parse state per-instance instead of on the class, so
  concurrent cover/metadata parses no longer corrupt each other.
- Library scan no longer loops forever on a symlink cycle: `scan_all()` prunes
  directories whose real path was already walked.
- INPX parser hardened: INP records are decoded with `errors='replace'` (a
  cp1251 collection no longer aborts the scan), zip/INP handles are closed via
  `with`, and an untrusted `FOLDER` value can no longer escape the collection
  directory (path traversal). First test coverage for the INPX parser.
- OPDS Basic-auth no longer creates a persistent session on every request, so
  the `django_session` table stops growing under e-reader polling.
- **Security:** decompression-bomb guard — book reads are capped at
  `MAX_BOOK_BYTES` and zip members declaring more than the cap are skipped
  instead of decompressed. `processzip` also releases handles via `with` and
  no longer lets one unreadable archive member abort the archive.
- **Security:** the FB2/EPUB parsers (lxml and expat) no longer resolve XML
  entities, closing an XXE local-file-disclosure and a billion-laughs DoS on
  crafted book files (scan path and the in-browser FB2 reader).
- A duplicate book/catalog row no longer aborts the whole scan: `findbook`
  and `findcat` return the first match instead of raising
  `MultipleObjectsReturned`.
- The scanner takes a cross-process `flock` before scanning, so an overlapping
  run (cron + manual) can no longer corrupt the `avail` sweep and delete live
  books. `apscheduler` is now imported lazily (only for `start`).
- `BSAddView`/`ThemeView` no longer 500 when the request has no `Referer`
  header; they fall back to the main page.
- Cleanups: fixed the always-true `assert` in `translit`, a loose INPX
  filename regex, `books_del_phisical` returning a tuple instead of a count,
  removed the dead `findauthor`, and silenced invalid-escape SyntaxWarnings.
- Robustness: book annotations and MOBI titles decode with `errors='replace'`
  (a non-utf8 payload no longer fails the import); cover thumbnailing is capped
  by `Image.MAX_IMAGE_PIXELS` and falls back to the no-cover image on any
  decode error (PIL decompression-bomb guard).
- The library scan now commits per directory (with the delete-sweep kept
  atomic) instead of wrapping the whole walk in one transaction, so an
  interrupted scan keeps its progress and holds no multi-hour lock.

### Security
- Env-gated production security settings: `SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`
  (+ subdomains/preload), `SECURE_PROXY_SSL_HEADER` and `CSRF_TRUSTED_ORIGINS`.
  All default off (behaviour unchanged); enable when served over HTTPS.
- Regression test locking in that `alphabet_menu()` passes user input as bound
  parameters (no SQL injection) and escapes LIKE wildcards.
