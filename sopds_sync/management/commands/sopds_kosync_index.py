# -*- coding: utf-8 -*-
#
# Precompute the KOReader document hashes for the catalogue, so kosync progress
# arriving from an e-reader can be attributed to a Book.
#
# kosync sends only a hash of the copy on the device, so the only way to know
# which book it is, is to have already worked out what our own files hash to.
# See sopds_sync.digest for the two hashing methods and their exact definition.
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
# Run after a scan: python manage.py sopds_kosync_index [--dry-run] [--limit N]
import logging

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from opds_catalog import dl, utils
from opds_catalog.models import Book
from constance import config

from sopds_sync.digest import filename_md5, partial_md5
from sopds_sync.models import BookDigest


def download_names(book):
    """Base names this book can carry on a device after an OPDS download.

    `dl.getFileName` returns whichever one SOPDS_TITLE_AS_FILENAME currently
    selects; index both, because the setting may well have been flipped since
    the reader fetched the book.
    """
    from_title = utils.to_ascii(utils.translit(book.title + '.' + book.format))
    from_file = utils.to_ascii(utils.translit(book.filename))
    return {n for n in (from_title, from_file, book.filename) if n}


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
        limit = options['limit']
        rebuild = options['rebuild']
        skip_binary = options['skip_binary']
        verbose = options['verbose']
        logger = logging.getLogger('')

        close_old_connections()
        qs = Book.objects.all().order_by('id')
        if not rebuild:
            qs = qs.filter(digests__isnull=True)

        indexed = added = unreadable = collisions = 0
        for book in qs.iterator(chunk_size=200):
            if limit and indexed >= limit:
                break
            indexed += 1

            wanted = {name: BookDigest.FILENAME for name in
                      (filename_md5(n) for n in download_names(book)) if name}

            if not skip_binary:
                data = dl.getFileData(book)
                if data is None:
                    unreadable += 1
                else:
                    wanted[partial_md5(data)] = BookDigest.BINARY

            if rebuild and not dry_run:
                BookDigest.objects.filter(book=book).exclude(digest__in=wanted).delete()

            for digest, method in wanted.items():
                if dry_run:
                    if not BookDigest.objects.filter(digest=digest).exists():
                        added += 1
                    continue

                # `digest` is unique: another book hashing the same way is the
                # same bytes or the same name, and the first one to claim it
                # keeps it. Attributing progress to one of two identical copies
                # is right either way; guessing between them is not.
                _row, created = BookDigest.objects.get_or_create(
                    digest=digest, defaults={'book': book, 'method': method})
                if created:
                    added += 1
                elif _row.book_id != book.id:
                    collisions += 1
                    logger.debug('Digest %s already claimed by book %s (not %s)',
                                 digest, _row.book_id, book.id)

            if verbose:
                self.stdout.write('Book %s (%s): %d digest(s)'
                                  % (book.id, book.filename, len(wanted)))

        prefix = 'DRY-RUN: ' if dry_run else ''
        self.stdout.write(
            '%skosync index done. Books: %d, digests %s: %d, '
            'files unreadable: %d, already claimed: %d%s'
            % (prefix, indexed, 'would be added' if dry_run else 'added', added,
               unreadable, collisions,
               '' if not skip_binary else ' (content digests skipped)')
        )
        if config.SOPDS_KOSYNC_ENABLE is False:
            self.stdout.write('Note: SOPDS_KOSYNC_ENABLE is off, so nothing will use this yet.')
