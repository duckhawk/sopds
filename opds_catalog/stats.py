# -*- coding: utf-8 -*-
"""Recording and reading how often books are taken out of the library.

Nothing here may ever be the reason a download fails, so `record` swallows its
own errors: a counter is worth less than the file the reader came for.

See :class:`opds_catalog.models.BookStat` for why these are aggregate counters
rather than a log of events.
"""
import logging

from django.db.models import F
from django.utils import timezone

from opds_catalog.models import Book, BookStat

logger = logging.getLogger(__name__)

DOWNLOADS = 'downloads'
READS = 'reads'


def record(book_id, kind):
    """Add one to a book's counter. Never raises."""
    if kind not in (DOWNLOADS, READS):
        raise ValueError('unknown counter %r' % kind)

    try:
        now = timezone.now()
        # One UPDATE in the common case, and an atomic one — two readers
        # downloading at once must not lose a count to a read-modify-write.
        updated = (BookStat.objects
                   .filter(book_id=book_id)
                   .update(**{kind: F(kind) + 1, 'last_used': now}))
        if not updated:
            BookStat.objects.get_or_create(
                book_id=book_id, defaults={kind: 1, 'last_used': now})
    except Exception:
        logger.exception('Could not record a %s for book %s', kind, book_id)


def summary(book_ids):
    """`{book_id: {'downloads': int, 'reads': int}}` for the books with counts.

    Books nobody has touched are absent rather than present as zeroes, matching
    `ratings.summary` so a template can treat both the same way.
    """
    book_ids = list(book_ids)
    if not book_ids:
        return {}

    rows = BookStat.objects.filter(book_id__in=book_ids).values(
        'book_id', 'downloads', 'reads')
    return {r['book_id']: {'downloads': r['downloads'], 'reads': r['reads']}
            for r in rows if r['downloads'] or r['reads']}


def most_popular():
    """Books ordered by download count, most first.

    Books nobody has downloaded are excluded: a listing of untouched books
    ordered by zero is not a "popular" listing. Reads break a tie, then the
    title, so paging stays stable.
    """
    return (Book.objects
            .filter(stat__downloads__gt=0)
            .order_by('-stat__downloads', '-stat__reads', 'search_title'))
