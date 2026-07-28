"""Turning a kosync document hash into a catalogue Book, and back into a shelf.

Kept out of :mod:`sopds_sync.kosync` so the protocol handlers stay about the
protocol, and so every step here can be exercised without an HTTP request.
"""
import logging

from django.utils import timezone

from opds_catalog.models import bookshelf

from .models import BookDigest

logger = logging.getLogger(__name__)

# At or above this fraction the book counts as finished. KOReader rarely reports
# a clean 1.0 — the last page of a reflowed book lands a hair short — so the
# threshold sits just below it rather than at it.
FINISHED_AT = 0.99


def resolve_book(document):
    """The Book a kosync document hash refers to, or None if we don't know it.

    Unknown hashes are the normal case for a book the reader did not get from
    this catalogue, and for one whose digests have not been indexed yet.
    """
    if not document:
        return None

    row = (BookDigest.objects
           .filter(digest=document)
           .select_related('book')
           .first())
    return row.book if row else None


def resolve_book_by_name(name):
    """The Book a file name on a reading device refers to, or None.

    Moon+ Reader names a position file after the book file as it exists on the
    phone, which is the only identification the format offers. Three ways in,
    cheapest first:

    1. The digest index :mod:`sopds_sync.indexing` already maintains. It holds
       an md5 of every name a book can carry after an OPDS download, so a book
       fetched from this catalogue is one indexed lookup away — the same table
       that resolves KOReader hashes, reused rather than rebuilt.
    2. The catalogue's own file name, for a library whose files were named the
       way the catalogue names them.
    3. "Title - Author" — what a book downloaded from a public library site is
       usually called. Only used when the first two miss, and only when it picks
       out exactly one book: guessing between editions would attribute reading
       progress to the wrong one.
    """
    from opds_catalog.models import Book

    from .digest import filename_md5

    if not name:
        return None

    # Moon+ Reader reads fb2.zip without unpacking, so the name it records can
    # carry the .zip our download appends.
    variants = [name]
    if name.lower().endswith('.zip'):
        variants.append(name[:-len('.zip')])

    for variant in variants:
        book = resolve_book(filename_md5(variant))
        if book is not None:
            return book

    for variant in variants:
        book = Book.objects.filter(filename=variant).first()
        if book is not None:
            return book

    for variant in variants:
        stem = variant.rsplit('.', 1)[0] if '.' in variant else variant
        if ' - ' not in stem:
            continue
        title, _, author = stem.partition(' - ')
        title, author = title.strip(), author.strip()
        if not title:
            continue
        # "Surname Firstname" is exactly how the catalogue stores an author, so
        # the whole half matches as it stands; falling back to every word being
        # present covers a middle name the file name dropped.
        query = Book.objects.filter(title__iexact=title)
        if author:
            by_full = query.filter(authors__full_name__iexact=author)
            if by_full.exists():
                query = by_full
            else:
                for part in author.split():
                    query = query.filter(authors__full_name__icontains=part)
        found = list(query.distinct()[:2])
        if len(found) == 1:
            return found[0]

    return None


def status_for(percentage):
    """The bookshelf status implied by a reported progress fraction."""
    if percentage >= FINISHED_AT:
        return bookshelf.STATUS_READ
    if percentage > 0:
        return bookshelf.STATUS_READING
    return ''


def record_progress(user, book, percentage, when=None):
    """Reflect e-reader progress onto the user's shelf entry for `book`.

    Only ever moves a book forward: a device that reports an earlier position
    (a re-read, or a stale sync from a second device) must not undo a "read"
    mark or wind the percentage back. Returns the shelf row, or None if there
    was nothing to record.
    """
    if book is None:
        return None

    percentage = max(0.0, min(1.0, float(percentage or 0.0)))
    shelf, _created = bookshelf.objects.get_or_create(user=user, book=book)

    fields = []
    if shelf.percent is None or percentage > shelf.percent:
        shelf.percent = percentage
        fields.append('percent')

    status = status_for(percentage)
    # 'reading' must not overwrite 'read'; an explicit choice by the user is not
    # overwritten by a lower one either.
    rank = {'': 0, bookshelf.STATUS_TO_READ: 0,
            bookshelf.STATUS_READING: 1, bookshelf.STATUS_READ: 2}
    if rank.get(status, 0) > rank.get(shelf.status, 0):
        shelf.status = status
        fields.append('status')

    if fields:
        shelf.readtime = when or timezone.now()
        fields.append('readtime')
        shelf.save(update_fields=fields)

    return shelf
