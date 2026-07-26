# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **A rate limit on the routes that hand out book content** — downloads,
  conversions, covers, thumbnails, the reader and its illustrations
  (`SOPDS_RATE_LIMIT`, 600 requests per minute per reader, 0 disables). Only
  the login form was throttled, which mattered less when every content route
  was a file read and matters more now that one of them unzips a book and
  parses every document in its spine. This is not access control — the reader
  is already authenticated by then — it is a ceiling so a runaway sync loop or
  a mirroring script degrades itself rather than the server. A signed-in
  reader is counted as themselves rather than as their address, since a
  household behind one NAT is several readers; the counter lives in the shared
  cache so the limit is one limit across workers; the check runs before the
  ETag and the cache, so a client over its budget cannot spend anything; and a
  cache outage lifts the limit rather than refusing the library.
  Browsing the feeds is not throttled — it is handing out content that costs.
- **Scans are recorded.** The scanner counted books added, removed, skipped and
  unparseable, then wrote the numbers to a log file and forgot them, so the
  outcome of a scan was invisible to the application — including whether it
  finished at all. A crashed scan left only a stack trace somewhere and a
  catalogue that had quietly stopped growing. Each run now opens a `ScanRun`
  row and closes it with its counters and its outcome, a failure is recorded
  as a failure, and the history is browsable read-only in the admin. The
  metrics endpoint reports the last run's success, duration, books added and
  removed, unparseable files, and whether a scan is in progress. Reporting
  swallows its own errors throughout: observability is not worth failing the
  thing being observed. Migration `0024`.
- **Prometheus metrics at `/metrics`**, off by default
  (`SOPDS_METRICS_ENABLE`), with an optional bearer token
  (`SOPDS_METRICS_TOKEN`). Until now the only observability was a liveness and
  a readiness probe, so a deployment could not answer how big the catalogue
  is, how much is being taken out of it, or — the failure this catalogue
  actually has — whether the scan is still running at all.
  Everything exposed is derived from the database on scrape rather than
  accumulated in the process: the app runs several uwsgi workers, so an
  in-process counter would describe only whichever worker answered, and
  getting that right means `prometheus_client`'s multiprocess mode with its
  shared directory and per-worker cleanup. Gauges read from the DB are the
  same number whoever answers. Per-request latency and rate are genuinely
  per-process and are left to an ingress or sidecar that sees every request.
  `lectern_last_scan_timestamp_seconds` is absent rather than zero when no
  scan has ever finished, so an alert on staleness can tell the two apart.
  The body is cached briefly, because several of the gauges are `COUNT()`s
  over the whole book table and scrapes are frequent.
- `sopds_enrich` now also gives a book its **authors**, when it has none and
  Open Library knows them. A book whose file carried no author metadata was
  previously unreachable through the author browser and the author search;
  now it is. An author that is already recorded is never replaced, not even
  under `--force` — the parser read that name out of the file itself, one ISBN
  can cover editions credited differently, and overwriting a known author with
  a remote guess is very hard to undo once it has run over a whole catalogue.
  Author rows are reused rather than duplicated, get their search name filled
  so the new author is findable, and are capped per book.
  Cover images are still not fetched: that needs either a third-party request
  from every reader's browser — which the OPDS descriptor was just cured of —
  or local storage this project does not yet have, so it wants its own change.
- **Genres can be searched by name**, in OPDS (`/opds/search/genres/m/<term>/`,
  and an entry in the OPDS search menu) and in the web UI (a *Genre* option in
  the search-type chooser). They were browsable through the section tree but
  not searchable, so reaching a leaf like "Detective" meant already knowing
  which section it sits under. Matching is on the leaf name a reader would
  actually type, with the section shown alongside for context; results link
  straight to the books in that genre. Modelled on the existing author and
  series searches rather than as another single-letter book-search type,
  because the result is a list of genres to choose from, not a list of books.
- The bookshelf can be narrowed by reading status
  (`/web/search/books/?searchtype=u&status=reading`). Status has been set by
  hand, and by an e-reader syncing progress since the kosync link, but the
  shelf could only ever be shown whole. The filter is carried through
  pagination, and an unrecognised value shows the whole shelf rather than an
  unexplained empty one.

### Removed
- `Cover0`, the pre-0.41 FB2-only cover extractor. Nothing had referenced it
  since; its only remaining mention was its own definition. The `base64` and
  `fb2parse` imports it alone kept alive went with it.

## [0.50.0] - 2026-07-26

First release of the Lectern fork. Everything below has accumulated since
**v0.49** (September 2022): the rebrand and UI rewrite, the move to Django 5.2,
a long run of scanner and parser hardening, several security fixes, and the
reading, sync and metadata features added on top.

Versions are three-part from here on. The `v0.50` tag in this repository
belongs to the unrelated `sarutobi/sopds-ng` fork, which is why our own
sequence continues at `v0.50.0` rather than reusing that name.

### Upgrading

Run `python manage.py migrate` — this release adds migrations `0011`–`0023`
in `opds_catalog`, and the whole of the new `sopds_sync` app (`0001`, `0002`).
Two of them (`0016`, `0020`) build indexes over `opds_catalog_book` and hold a
write lock on it for the duration, so pick a scan-free window on a large
catalogue.

Optional, once migrated:

- `python manage.py sopds_isbn_backfill` — fill `isbn` for books already in
  the catalogue (a normal incremental scan skips them).
- `python manage.py sopds_enrich` — fill empty annotations, dates and
  publishers from Open Library, for books that have an ISBN.
- `python manage.py sopds_kosync_index` — build the KOReader digest index for
  the existing catalogue. Only needed once; scans keep it current afterwards.

### Added
- **Download and read statistics, and a "Most popular" listing.** Nothing
  recorded what the library was actually used for, so the one entry point still
  missing from the catalogue — what everyone else is reading — could not be
  built. Downloads (including conversions) and reader opens are now counted per
  book, shown on the card and in the OPDS entry, and a **Most popular** listing
  (search type `p`) joins *Recently added* and *Top rated* in the root feed and
  the navigation. Migration `0023`.
  These are aggregate counters, not a log of events: a per-request table would
  grow without bound under e-reader polling and would amount to a reading
  history for every user, which is a far bigger thing to store than "how
  popular is this book". Nothing records *who* took what — a user's own history
  already lives in `bookshelf`, where they can see and clear it. Counting is
  atomic, and it can never fail a download: a counter is worth less than the
  file the reader came for. Russian translations included, with plural forms.
- **EPUB can be read in the browser.** Until now the reader was FB2-only; #66
  had removed the Read link everywhere else rather than leave it leading to a
  500. Instead of bolting a second, client-side reader onto the page, the EPUB
  is rendered server-side into the same flat stream of numbered paragraphs and
  `TOC_n` chapter markers the FB2 stylesheet emits, so remembered position,
  the progress bar, chapter mode and the font-size preference all work on EPUB
  with no change to the reader and no new JavaScript dependency.
  Illustrations are served from inside the archive by a new
  `/opds/read/<id>/res/<path>` route. Because the markup comes from the book,
  it is rebuilt against an allowlist rather than filtered: scripts, styles,
  embedded objects, event handlers and `javascript:`/`data:`/`file:` links do
  not survive, the book's own `class`/`style`/`id` are dropped so it cannot
  restyle the reader or collide with the position ids, and an `<img>` is kept
  only if it resolves to something the archive actually holds. PDF and DjVu
  still download rather than open; those need a real client-side viewer.
- **Ratings are aggregated across readers.** `bookshelf.rating` had been
  collected per user for a while but only ever read back to redraw that same
  user's own stars: nobody could see what the library as a whole thought of a
  book, and nothing could be ordered by it. The book card and the OPDS entry
  now show the average and the number of votes next to your own stars, and a
  **Top rated** listing (search type `r`) joins *Recently added* in the OPDS
  root feed and the web navigation. Unrated books are left out of it rather
  than sorted as zeroes, and the vote count breaks a tie on the average, so a
  book five people rated 5 outranks one a single person did. The aggregate is
  one extra query per page keyed on the ids already in hand, so it does not
  perturb the three different ways the book list is assembled. Russian
  translations included, with plural forms.
- **Recently added** listing — the "what's new" entry point the catalogue was
  missing. It is the first item of the OPDS root feed (`/opds/search/books/n/0/`,
  search type `n`) and the first item of the web navigation
  (`/web/search/books/?searchtype=n`), paginated like any other book list and
  ordered by registration date, newest first. Migration `0020` adds the
  matching `(registerdate DESC, id DESC)` index so paging through a large
  catalogue reads the order off the index instead of sorting the table.
- **Search by ISBN.** The ISBN extracted during the scan was stored and then
  never used — it was neither shown nor searchable. It is now a search type of
  its own (`x`) in both the web UI and OPDS, offered in the OPDS search menu
  when the term is actually an ISBN, and shown on the book card as a link that
  finds the other editions of the same book. Pasting an ISBN into the ordinary
  title box searches the ISBN instead of coming back empty; terms are
  normalised, so hyphenated, spaced and `ISBN`-prefixed forms all work.
  Russian translations included.
- **`sopds_enrich` — metadata from Open Library, keyed on the ISBN.** FB2/EPUB
  files from a typical collection carry a title, an author and little else, and
  no format we parse records a publisher at all. Where a book has an ISBN, the
  command fills its empty annotation, publication date and publisher from the
  Open Library Books API. Only empty fields are filled — the file is the
  authority on its own contents, and an ISBN can be shared by editions that
  differ in the details — unless `--force` is given. Migration `0020` adds the
  `publisher` and `enriched` columns; the latter keeps a re-run from asking
  about books already tried, including the ones Open Library had nothing for.
  A lookup that fails (timeout, 5xx, malformed reply) is reported as a failure
  rather than a miss, so an outage leaves those books queued for the next run.
  `--dry-run`, `--limit`, `--batch-size` and `--sleep` (default 1s between
  calls) as usual. Russian translations included.
- **kosync progress now names a catalogue book.** The protocol identifies a
  book only by a hash KOReader computes on the device, so the two halves of
  the app each tracked reading progress without knowing about the other: read
  a chapter on the e-reader and the web UI had no idea. `sopds_kosync_index`
  precomputes the hashes our own files produce — the partial content md5 and
  the file-name md5, for both names an OPDS download can arrive under — and
  incoming progress is matched against them. A recognised book gets its
  `KosyncProgress.book` set, and the user's shelf entry picks up the
  percentage and moves to *reading* / *read*; the book list shows how far the
  e-reader has got. Progress only ever moves forward, so a second device
  syncing a stale position cannot undo the first. An unrecognised hash still
  syncs exactly as before — kosync remains a key-value store first — and a
  failure on the shelf side can never fail the sync itself. Migrations
  `opds_catalog/0020` and `sopds_sync/0002`. Run the indexer after a scan.
  Russian translations included.
- htmx live-search suggestions in the header search box (title/author/series),
  as progressive enhancement.
- Title suggestions now show the book's first author next to the title, so
  same-/similar-titled books are distinguishable.
- pytest / pytest-django test suite with coverage; GitHub Actions CI running
  the suite on Python 3.12, plus a non-blocking `ruff` lint pass.
- Tooling config in `pyproject.toml` (ruff, mypy); `CONTRIBUTING.md`.

### Changed
- The EPUB reader no longer re-does its work on every request. Rendering a book
  means unzipping it and parsing, sanitising and serialising every document in
  its spine — a few hundred kilobytes of HTML — and the page then asked for each
  illustration separately, **each of which re-read the entire archive** through
  `getFileData`. A chapter with twenty pictures therefore read a 5 MB book
  twenty-one times. The render and the images are now cached on the same content
  validator the covers use, both routes answer `If-None-Match` with a 304, and a
  book that *is* the archive is opened from disk instead of being slurped into
  memory first, so only the members actually wanted get decompressed.
- Covers and thumbnails answer conditional GETs. `/opds/cover/<id>/` and
  `/opds/thumb/<id>/` now send an `ETag` derived from the containing file's
  size and mtime — one `stat()`, no unzipping — so a reader revalidating with
  `If-None-Match` gets a bodyless **304** instead of a re-extracted JPEG.
  Behind that, the body cache is keyed on the ETag rather than on the URL as
  `cache_page` was: replacing a book in place now invalidates its cover at
  once instead of serving the previous one until `SOPDS_CACHE_TIME` ran out.
  A book with no readable cover is cached as such, so it is not re-parsed on
  every page view, and the responses are marked `Cache-Control: public` (they
  carry no per-user content). `SOPDS_CACHE_TIME` is read per request, so
  changing it in the admin no longer needs a restart.
- Incremental scan no longer rewrites the whole `Book` table on every run. The
  start-of-scan `UPDATE opds_catalog_book SET avail=1` full-table sweep is
  replaced by a scratch `ScanSeen` table: the scanner records the id of each
  book it re-finds or adds, and the post-walk pass deletes only the books not
  seen (anti-join). Unchanged books are never rewritten — no bloat and no
  read-slowdown during a scan — and the sweep refuses to run if the seen set is
  empty (never wipes a non-empty catalogue). Adds migration `0017`.
- Upgraded to Django 5.2 LTS (Python 3.13 / PostgreSQL supported).
- Test dependencies are constrained by `requirements.txt` (`requirements-test.txt`
  uses `-c`), so the test environment never drifts from production.

### Fixed
- The kosync digest index is now refreshed by the scan. It was only ever built
  by running `sopds_kosync_index` by hand, so every book added afterwards was
  invisible to the matcher: progress from an e-reader still synced, it just
  stopped being attributed to a book, and nothing said so. The scanner indexes
  what it added at the end of each run — idempotent, a no-op when kosync is
  off, and it swallows its own errors, because failing to index digests must
  not fail the scan that produced them.
- The whole web UI returned **500 for every visitor when `SOPDS_AUTH` is off**.
  `sopds_login` lets anonymous visitors through in that configuration — which
  is the point of it — but every view then called `theme_css(request.user)`,
  which filtered `Theme` by an `AnonymousUser` (`TypeError: Field 'id'
  expected a number`). Browsing pages and the reader now fall back to the
  default theme and preferences, and the genuinely per-user endpoints (theme
  toggle, settings, device sync, bookshelf add/delete/clear, reading position,
  status, rating) answer **403** instead of raising, since with authentication
  off there is no user to own that data.
- The **Read** link was rendered for every book, but the in-browser reader is
  an FB2-to-XHTML transform: clicking it on an EPUB, MOBI, PDF or DjVu fed a
  binary container to the XML parser and returned **500**. Both reader views
  now raise `Http404` for anything but FB2 (matching the guard `ConvertFB2`
  already had), and the book list only offers the link where it works.
- `/opds/thumb/` (the `covertmpl` route) returned **500**: it was wired to
  `Cover`, which takes a mandatory `book_id`, so requesting it raised
  `TypeError`. It now serves the no-cover placeholder it was meant to.
- **Security:** the OIDC client secret and Telegram API token are entered
  through a masked password field in the constance admin instead of being
  shown in clear text.
- Narrowed bare `except:` clauses to `except Exception:` in the download views
  and the FB2/EPUB parsers, so `KeyboardInterrupt`/`SystemExit` propagate
  instead of being swallowed.
- Removed the empty, unused `opds_catalog/views.py`.
- `search_title`/`search_full_name`/`search_ser`/`full_name` default to `''`
  instead of `None` (they are `NOT NULL` and always populated). Migration `0018`.
- Scanner refreshes a stale connection via `connection.close()` instead of
  deleting `connections._connections` internals (in `check_settings` too).
- **Security:** open redirect in `LoginView` (`?next=` is now validated with
  `url_has_allowed_host_and_scheme`).
- Hardened the book-conversion route: `convert_type` restricted to `epub|mobi`
  and `ConvertFB2` raises `Http404` for anything else (was a 500).
- Return **404 instead of 500** for missing or non-numeric ids across the
  download/convert/read/cover views, the genre and "doubles" search views, and
  the corresponding OPDS feeds.
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
- Scan mutual-exclusion uses a PostgreSQL **session advisory lock** (with a
  pidfile-flock fallback on other backends): it is released automatically when
  the scanner process/pod dies, so an interrupted scan can no longer overlap
  the next one across restarts.
- The scanner runs `VACUUM (ANALYZE)` on the catalog tables after each scan, so
  the full-table `avail` sweep no longer leaves the alphabet-menu queries
  slow (lost Index-Only Scan) until autovacuum catches up.

### Security
- **Catalogue content was reachable without authentication.** With
  `SOPDS_AUTH` on, the OPDS feeds and `/opds/download/` correctly answered
  401, but `/opds/cover/`, `/opds/thumb/`, `/opds/convert/` and — worst of the
  four — `/opds/read/` did not: the reader route handed out the **full text of
  any book** to anyone who knew (or guessed) an id, and the cover routes
  exposed cover art, which usually carries the title and author. All four now
  go through the same `require_catalog_access` guard `Download` had inline,
  accepting either a session login or OPDS Basic auth. The check runs outside
  the cover ETag and cache, so an anonymous request cannot spend CPU
  unzipping books or populate the cache either. The book-less no-cover
  placeholder stays open — it carries nothing from the catalogue.
- Brute-force throttle on the web login form: after 10 failed attempts per
  client IP the login is locked out for 5 minutes (shared cache, so it holds
  across workers). A successful login clears the counter.
- Regression test locking in that `alphabet_menu()` passes user input as bound
  parameters (no SQL injection) and escapes LIKE wildcards.
