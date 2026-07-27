# Running your own

Lectern is free software under the GPL. The source, installation instructions
and issue tracker are at
[github.com/duckhawk/lectern](https://github.com/duckhawk/lectern).

What follows is an outline for whoever administers an installation, not a
substitute for the README in the repository.

## The shape of it

A Django application, PostgreSQL for the catalogue, Redis for the page cache,
and a directory of book files it never writes to. Books can be loose files, or
inside zip archives, or listed in an INPX index — the scanner handles all
three.

Everything that can be configured at run time lives in the Django admin under
**Constance**, which means changing it does not need a restart or a redeploy.

## Scanning

`python3 manage.py sopds_scanner start` runs the scanner on the schedule set in
the admin (`SOPDS_SCAN_SHED_*`, cron syntax), or `scan`/`rescan` runs it once.
It reads metadata out of each book, and builds the digest index that lets
KOReader progress name a book.

`SOPDS_ROOT_LIB` is the directory it walks, and `SOPDS_BOOK_EXTENSIONS` which
files it considers books.

## Converters

Optional, all configured in the admin:

- `SOPDS_FB2TOEPUB`, `SOPDS_FB2TOMOBI` — external programs; when set, FB2 books
  are additionally offered in those formats.
- `SOPDS_DJVUTOPDF` — `ddjvu` from djvulibre, which is what makes DjVu readable
  in the browser. Keep the `-quality` argument in the default command: without
  it a photographic scan converts to lossless raster and a hundred pages can
  come to a gigabyte.

## Accounts and mail

`SOPDS_AUTH` decides whether the catalogue needs a login at all;
`SOPDS_ALLOW_REGISTRATION` whether visitors can create their own accounts.
Filling in the `SOPDS_SMTP_*` settings enables password resets and sending books
to a device. `SOPDS_OIDC_*` puts a Keycloak button on the login page.

## Backups

The book files are on disk and a rescan rebuilds the catalogue from them, but a
rescan cannot rebuild what readers put in: shelves, statuses, ratings, reading
positions, tags, lists, counters, and the metadata `sopds_enrich` fetched.

    python3 manage.py sopds_userdata_export backup.json
    python3 manage.py sopds_userdata_import backup.json

This keys on identity that survives a rebuilt catalogue — the path and filename
the scanner itself uses, with a content digest as the fallback — because book
ids change and a restore keyed on them would name the wrong books.

## Monitoring

`/healthz` and `/readyz` for probes, and `/metrics` in Prometheus format when
`SOPDS_METRICS_ENABLE` is on (optionally behind a bearer token). The metrics are
derived from the database, so every worker reports the same numbers.
