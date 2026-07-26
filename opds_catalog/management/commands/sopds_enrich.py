# -*- coding: utf-8 -*-
#
# Fill in catalogue metadata from Open Library, keyed on Book.isbn.
#
# FB2 and EPUB files from a typical collection carry a title, an author and
# little else: the annotation is empty, the publication date is a bare year or
# missing, and no format we parse records a publisher. Where the file does have
# an ISBN (extracted by the scanner since #63, backfilled by sopds_isbn_backfill)
# Open Library can supply the rest.
#
# Only empty fields are filled, so a value that came out of the book file always
# wins over the remote one — the file is the authority on its own contents, and
# an ISBN can be shared by editions that differ in the details. --force overrides
# that for a deliberate refresh.
#
# Run: python manage.py sopds_enrich [--dry-run] [--limit N] [--force]
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from opds_catalog import openlibrary
from opds_catalog.models import (
    Book, SIZE_BOOK_ANNOTATION, SIZE_BOOK_DOCDATE, SIZE_BOOK_PUBLISHER,
)

# Field -> column width, so a long remote value is truncated rather than
# rejected by the DB.
FIELD_LIMITS = {
    'annotation': SIZE_BOOK_ANNOTATION,
    'docdate': SIZE_BOOK_DOCDATE,
    'publisher': SIZE_BOOK_PUBLISHER,
}


class Command(BaseCommand):
    help = 'Fill empty annotation/publisher/date fields from Open Library, by ISBN.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Report what would change without writing to the DB.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Process at most N books (0 = all).')
        parser.add_argument('--force', action='store_true', default=False,
                            help='Overwrite fields that already have a value, and '
                                 're-query books enriched by an earlier run.')
        parser.add_argument('--batch-size', type=int, default=openlibrary.MAX_BATCH,
                            help='ISBNs per API call (default %d).' % openlibrary.MAX_BATCH)
        parser.add_argument('--sleep', type=float, default=1.0,
                            help='Seconds to wait between API calls (default 1.0). '
                                 'Open Library is a free service; do not hammer it.')
        parser.add_argument('--verbose', action='store_true', default=False,
                            help='Log every updated book.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        force = options['force']
        batch_size = max(1, min(options['batch_size'], openlibrary.MAX_BATCH))
        sleep = max(0.0, options['sleep'])
        verbose = options['verbose']
        logger = logging.getLogger('')

        close_old_connections()
        books = self.candidates(force)
        if limit:
            books = books[:limit]
        books = list(books)

        looked_up = updated = not_found = nothing_new = failed = 0
        # Only books whose batch actually reached the API get stamped as tried; a
        # batch lost to a timeout has to stay a candidate for the next run.
        answered = []
        for start in range(0, len(books), batch_size):
            batch = books[start:start + batch_size]
            # One ISBN can be shared by several catalogue rows (the same book in
            # two formats, or a duplicate); query it once and apply to each.
            by_isbn = {}
            for book in batch:
                by_isbn.setdefault(book.isbn, []).append(book)

            if start and sleep:
                time.sleep(sleep)

            found = openlibrary.fetch(list(by_isbn))
            if found is None:
                failed += len(by_isbn)
                continue

            looked_up += len(by_isbn)
            answered.extend(batch)

            for isbn, rows in by_isbn.items():
                fields = found.get(isbn)
                if not fields:
                    not_found += 1
                    continue

                for book in rows:
                    changed = self.apply(book, fields, force)
                    if not changed:
                        nothing_new += 1
                        continue

                    updated += 1
                    if verbose:
                        self.stdout.write('Book %s (%s): %s'
                                          % (book.id, book.isbn, ', '.join(sorted(changed))))
                    if not dry_run:
                        changed['enriched'] = timezone.now()
                        Book.objects.filter(pk=book.pk).update(**changed)
                        logger.debug('Enriched book %s from Open Library', book.id)

        if not dry_run:
            # Stamp the misses too, so the next run does not ask about them again.
            self.mark_tried(answered)

        prefix = 'DRY-RUN: ' if dry_run else ''
        self.stdout.write(
            '%sEnrichment done. Candidates: %d, ISBNs looked up: %d, %s: %d, '
            'not in Open Library: %d, nothing to add: %d, lookups failed: %d'
            % (prefix, len(books), looked_up,
               'would update' if dry_run else 'updated', updated, not_found,
               nothing_new, failed)
        )

    def candidates(self, force):
        """Books worth asking Open Library about."""
        qs = Book.objects.exclude(isbn='')
        if force:
            return qs.order_by('id')
        # Skip anything a previous run already resolved or tried, and anything
        # that has nothing left to fill.
        has_a_gap = Q(annotation='') | Q(docdate='') | Q(publisher='')
        return qs.filter(enriched__isnull=True).filter(has_a_gap).order_by('id')

    def apply(self, book, fields, force):
        """Return the subset of `fields` this book should actually be updated with."""
        changed = {}
        for name, value in fields.items():
            if not value:
                continue
            if not force and getattr(book, name):
                continue
            new = value[:FIELD_LIMITS[name]]
            if new != getattr(book, name):
                changed[name] = new
        return changed

    def mark_tried(self, books):
        ids = [b.pk for b in books]
        for start in range(0, len(ids), 500):
            Book.objects.filter(pk__in=ids[start:start + 500],
                                enriched__isnull=True).update(enriched=timezone.now())
