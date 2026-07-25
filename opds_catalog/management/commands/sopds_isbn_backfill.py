# -*- coding: utf-8 -*-
#
# Backfill Book.isbn for books already in the catalogue.
#
# A normal (incremental) scan skips books already present in the DB, so the
# isbn column added in migration 0019 stays empty for the existing catalogue.
# This command re-reads each such book's file from disk, extracts the ISBN with
# the same parsers the scanner uses, and updates only the isbn field — without a
# full re-scan (registerdate, authors, genres and series are left untouched).
#
# Only fb2/epub are considered: those are the formats the parsers extract an
# ISBN from. Run: python manage.py sopds_isbn_backfill [--dry-run] [--limit N]
import logging

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from opds_catalog import dl
from opds_catalog.models import Book, SIZE_BOOK_ISBN
from book_tools.format import create_bookfile

# Formats whose metadata the parsers read an ISBN from.
ISBN_FORMATS = ['fb2', 'epub']


class Command(BaseCommand):
    help = 'Backfill Book.isbn from book files for catalogue entries missing it.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Report what would change without writing to the DB.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Process at most N books (0 = all).')
        parser.add_argument('--verbose', action='store_true', default=False,
                            help='Log every updated book.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        verbose = options['verbose']
        logger = logging.getLogger('')

        close_old_connections()
        qs = Book.objects.filter(isbn='', format__in=ISBN_FORMATS).order_by('id')

        scanned = updated = no_isbn = missing = errors = 0
        for book in qs.iterator(chunk_size=500):
            if limit and scanned >= limit:
                break
            scanned += 1

            data = dl.getFileData(book)
            if data is None:
                missing += 1
                continue

            try:
                book_data = create_bookfile(data, book.filename)
            except Exception as err:
                errors += 1
                logger.debug('ISBN backfill parse error for book %s: %s', book.id, err)
                continue

            isbn = (getattr(book_data, 'isbn', '') or '')[:SIZE_BOOK_ISBN]
            if not isbn:
                no_isbn += 1
                continue

            updated += 1
            if verbose:
                self.stdout.write('Book %s (%s): ISBN %s' % (book.id, book.filename, isbn))
            if not dry_run:
                Book.objects.filter(pk=book.pk).update(isbn=isbn)

        prefix = 'DRY-RUN: ' if dry_run else ''
        self.stdout.write(
            '%sISBN backfill done. Scanned: %d, %s: %d, no ISBN: %d, file missing: %d, parse errors: %d'
            % (prefix, scanned, 'would update' if dry_run else 'updated', updated, no_isbn, missing, errors)
        )
