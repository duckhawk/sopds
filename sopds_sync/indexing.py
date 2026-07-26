# -*- coding: utf-8 -*-
"""Building the BookDigest index that lets kosync progress name a book.

Shared by the `sopds_kosync_index` command and by the scanner, which runs it
over the newly added books at the end of every scan. Without that second caller
the index only ever covered the catalogue as it stood when an administrator last
ran the command by hand, and every book added afterwards silently stopped being
recognised — the sync kept working, it just no longer knew what was being read.
"""
import logging

from opds_catalog import dl, utils
from opds_catalog.models import Book

from sopds_sync.digest import filename_md5, partial_md5
from sopds_sync.models import BookDigest

logger = logging.getLogger(__name__)


def download_names(book):
    """Base names this book can carry on a device after an OPDS download.

    `dl.getFileName` returns whichever one SOPDS_TITLE_AS_FILENAME currently
    selects; index both, because the setting may well have been flipped since
    the reader fetched the book.
    """
    from_title = utils.to_ascii(utils.translit(book.title + '.' + book.format))
    from_file = utils.to_ascii(utils.translit(book.filename))
    return {n for n in (from_title, from_file, book.filename) if n}


def digests_for(book, skip_binary=False):
    """`{digest: method}` for one book, or as much of it as we can read."""
    wanted = {}
    for name in download_names(book):
        digest = filename_md5(name)
        if digest:
            wanted[digest] = BookDigest.FILENAME

    if skip_binary:
        return wanted, False

    data = dl.getFileData(book)
    if data is None:
        return wanted, True          # unreadable
    wanted[partial_md5(data)] = BookDigest.BINARY
    return wanted, False


def pending(rebuild=False):
    """Books that still need indexing."""
    books = Book.objects.all().order_by('id')
    if not rebuild:
        books = books.filter(digests__isnull=True)
    return books


def index(books, dry_run=False, rebuild=False, skip_binary=False,
          limit=0, on_book=None):
    """Index `books`, returning a counts dict.

    Idempotent: a book that already carries its digests costs one query and
    nothing else, which is what makes calling this after every scan cheap.
    """
    stats = {'books': 0, 'added': 0, 'unreadable': 0, 'collisions': 0}

    for book in books.iterator(chunk_size=200):
        if limit and stats['books'] >= limit:
            break
        stats['books'] += 1

        wanted, unreadable = digests_for(book, skip_binary=skip_binary)
        if unreadable:
            stats['unreadable'] += 1

        if rebuild and not dry_run:
            BookDigest.objects.filter(book=book).exclude(digest__in=wanted).delete()

        for digest, method in wanted.items():
            if dry_run:
                if not BookDigest.objects.filter(digest=digest).exists():
                    stats['added'] += 1
                continue

            # `digest` is unique: another book hashing the same way is the same
            # bytes or the same name, and the first to claim it keeps it.
            # Attributing progress to one of two identical copies is right
            # either way; guessing between them is not.
            row, created = BookDigest.objects.get_or_create(
                digest=digest, defaults={'book': book, 'method': method})
            if created:
                stats['added'] += 1
            elif row.book_id != book.id:
                stats['collisions'] += 1
                logger.debug('Digest %s already claimed by book %s (not %s)',
                             digest, row.book_id, book.id)

        if on_book is not None:
            on_book(book, wanted)

    return stats


def index_new_books():
    """Index whatever the last scan added. Safe to call unconditionally.

    Returns the counts dict, or None if the feature is off. Never raises: this
    runs at the tail of a scan, and a failure to index digests must not be able
    to fail the scan that produced them.
    """
    from constance import config

    if not config.SOPDS_KOSYNC_ENABLE:
        return None

    try:
        return index(pending())
    except Exception:
        logger.exception('Could not index KOReader digests for the new books')
        return None
