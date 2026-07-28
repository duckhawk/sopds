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


#: How many same-title editions are worth weighing against one position marker.
#: Each costs a parse of the whole book, and a shelf of a public library dump
#: can hold a startling number of copies of a popular novel.
MAX_CANDIDATES = 6


def resolve_books_by_name(name):
    """Catalogue books a file name on a reading device could refer to.

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
       usually called, and the only route left for a book that did not come
       from here at all.

    The first two identify a single file and return it alone. The third
    routinely does not: a catalogue built from a public library dump holds the
    same novel several times over, by the same author, under the same title, and
    the name on the phone cannot tell them apart. Rather than guess or give up,
    it returns every edition it found — the caller has a position marker, which
    is better evidence than the name ever was (see
    :func:`sopds_sync.moonsync.ingest`).
    """
    from opds_catalog.models import Book

    from .digest import filename_md5

    if not name:
        return []

    # Moon+ Reader reads fb2.zip without unpacking, so the name it records can
    # carry the .zip our download appends.
    variants = [name]
    if name.lower().endswith('.zip'):
        variants.append(name[:-len('.zip')])

    for variant in variants:
        book = resolve_book(filename_md5(variant))
        if book is not None:
            return [book]

    for variant in variants:
        book = Book.objects.filter(filename=variant).first()
        if book is not None:
            return [book]

    for variant in variants:
        stem = variant.rsplit('.', 1)[0] if '.' in variant else variant
        for title, author in _title_author(stem):
            # "Surname Firstname" is exactly how the catalogue stores an author,
            # so the whole half matches as it stands; falling back to every word
            # being present covers a middle name the file name dropped.
            query = Book.objects.filter(title__iexact=title)
            if author:
                by_full = query.filter(authors__full_name__iexact=author)
                if by_full.exists():
                    query = by_full
                else:
                    for part in author.split():
                        query = query.filter(authors__full_name__icontains=part)
            found = list(query.distinct().order_by('id')[:MAX_CANDIDATES])
            if found:
                return found

    return []


def _title_author(stem):
    """Ways to read a bare file name as a title and possibly an author.

    "Title - Author" first, because that is what the download links of the
    public library sites produce, then the whole name as a title: plenty of
    shelves hold books named after nothing but their title, and a title with a
    dash in it would otherwise be split down the middle and never found.
    """
    attempts = []
    if ' - ' in stem:
        title, _, author = stem.partition(' - ')
        if title.strip():
            attempts.append((title.strip(), author.strip()))
    if stem.strip():
        attempts.append((stem.strip(), ''))
    return attempts


def resolve_book_by_name(name):
    """The one book a device file name refers to, or None if it is not clear."""
    found = resolve_books_by_name(name)
    return found[0] if len(found) == 1 else None


def status_for(percentage):
    """The bookshelf status implied by a reported progress fraction."""
    if percentage >= FINISHED_AT:
        return bookshelf.STATUS_READ
    if percentage > 0:
        return bookshelf.STATUS_READING
    return ''


#: How definite each status is. Progress may raise a book's status but never
#: lower it: 'reading' must not undo 'read', and neither may overwrite a choice
#: the reader made by hand.
STATUS_RANK = {'': 0, bookshelf.STATUS_TO_READ: 0,
               bookshelf.STATUS_READING: 1, bookshelf.STATUS_READ: 2}


def raise_status(shelf, percentage):
    """Lift `shelf.status` to the one `percentage` implies. True if it moved."""
    status = status_for(percentage)
    if STATUS_RANK.get(status, 0) > STATUS_RANK.get(shelf.status, 0):
        shelf.status = status
        return True
    return False


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

    if raise_status(shelf, percentage):
        fields.append('status')

    if fields:
        shelf.readtime = when or timezone.now()
        fields.append('readtime')
        shelf.save(update_fields=fields)

    return shelf
