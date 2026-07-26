# -*- coding: utf-8 -*-
#
# Precompute the KOReader document hashes for the catalogue, so kosync progress
# arriving from an e-reader can be attributed to a Book.
#
# kosync sends only a hash of the copy on the device, so the only way to know
# which book it is, is to have already worked out what our own files hash to.
# See sopds_sync.digest for the two hashing methods and their exact definition,
# and sopds_sync.indexing for the indexing itself — which the scanner also runs
# over the books it just added, so a routine scan keeps the index current and
# this command is only needed for a first run or a --rebuild.
#
# Each book gets up to three digests: the partial content md5 (survives a
# rename), and the file-name md5 for both names a download can end up with —
# the transliterated title and the original file name, since
# SOPDS_TITLE_AS_FILENAME decides which one the OPDS download is served as.
#
# Reading is cheap: the binary digest samples twelve 1 KiB windows, so a book of
# any size costs a few KiB — but an archived book has to be decompressed up to
# the last sampled offset, so --skip-binary is there for a large zipped library.
#
# Run: python manage.py sopds_kosync_index [--dry-run] [--limit N] [--rebuild]
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from constance import config

from sopds_sync import indexing


class Command(BaseCommand):
    help = 'Index KOReader document hashes so kosync progress can name a book.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Report what would change without writing to the DB.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Process at most N books (0 = all).')
        parser.add_argument('--rebuild', action='store_true', default=False,
                            help='Recompute digests for books that already have some.')
        parser.add_argument('--skip-binary', action='store_true', default=False,
                            help='Only index file-name digests. Much faster on a large '
                                 'zipped library, but progress then only matches while '
                                 'the reader keeps the downloaded file name.')
        parser.add_argument('--verbose', action='store_true', default=False,
                            help='Log every indexed book.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        rebuild = options['rebuild']
        verbose = options['verbose']

        close_old_connections()

        def report(book, digests):
            if verbose:
                self.stdout.write('Book %s (%s): %d digest(s)'
                                  % (book.id, book.filename, len(digests)))

        stats = indexing.index(
            indexing.pending(rebuild=rebuild),
            dry_run=dry_run, rebuild=rebuild,
            skip_binary=options['skip_binary'], limit=options['limit'],
            on_book=report,
        )

        prefix = 'DRY-RUN: ' if dry_run else ''
        self.stdout.write(
            '%skosync index done. Books: %d, digests %s: %d, '
            'files unreadable: %d, already claimed: %d%s'
            % (prefix, stats['books'], 'would be added' if dry_run else 'added',
               stats['added'], stats['unreadable'], stats['collisions'],
               '' if not options['skip_binary'] else ' (content digests skipped)')
        )
        if not config.SOPDS_KOSYNC_ENABLE:
            self.stdout.write('Note: SOPDS_KOSYNC_ENABLE is off, so nothing will use this yet.')
