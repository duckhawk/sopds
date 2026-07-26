# -*- coding: utf-8 -*-
"""Export and restore the part of the database a rescan cannot rebuild.

Almost everything in the catalogue is derived: drop the database, run a scan,
and the books, authors, series and genres come back. These do not.

* what readers did — shelves, statuses, ratings, reading positions, progress
  synced from an e-reader, per-user reader preferences;
* what the library was used for — download and read counts;
* what enrichment cost — the ISBNs, publishers, annotations and dates
  `sopds_enrich` fetched, each of which was a request to somebody else's API.

`dumpdata` does not solve this. Every one of those tables points at `Book` by
id, and a rebuilt catalogue assigns different ids, so a restored shelf would
name the wrong books. This keys on identity that survives a rebuild instead:
the (path, filename) pair the scanner itself uses to recognise a book, with the
content digest as a fallback for a book that has since been renamed or moved.

Restoring never overwrites. A row that is already there is left alone unless
`force` is given, which makes an import safe to run against a live catalogue
and not only into an empty one.
"""
import logging

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from opds_catalog.models import Book, BookStat, Theme, bookshelf

logger = logging.getLogger(__name__)

VERSION = 1


def _iso(value):
    return value.isoformat() if value else None


def _book_key(book_digests, book):
    """How a book will be found again after the catalogue is rebuilt."""
    return {
        'path': book.path,
        'filename': book.filename,
        # Survives a rename, unlike the pair above, but only exists once
        # `sopds_kosync_index` has run.
        'digest': book_digests.get(book.id),
    }


def _content_digests():
    """{book_id: digest} for the books that have a content hash indexed."""
    try:
        from sopds_sync.models import BookDigest
    except Exception:      # pragma: no cover - the app is always installed
        return {}

    return dict(BookDigest.objects
                .filter(method=BookDigest.BINARY)
                .values_list('book_id', 'digest'))


def export():
    """Everything worth keeping, as a plain JSON-serialisable dict."""
    digests = _content_digests()
    books = {b.id: b for b in Book.objects.all()}

    payload = {
        'version': VERSION,
        'exported': _iso(timezone.now()),
        'books': [],
        'shelves': [],
        'themes': [],
        'kosync': [],
    }

    # Enrichment results and usage counters, per book.
    stats = {s.book_id: s for s in BookStat.objects.all()}
    for book in books.values():
        stat = stats.get(book.id)
        entry = _book_key(digests, book)
        entry.update({
            'isbn': book.isbn,
            'publisher': book.publisher,
            'annotation': book.annotation,
            'docdate': book.docdate,
            'enriched': _iso(book.enriched),
            'downloads': stat.downloads if stat else 0,
            'reads': stat.reads if stat else 0,
        })
        # A book with nothing enriched and nothing read carries no information
        # a rescan would not produce anyway.
        if any((entry['isbn'], entry['publisher'], entry['annotation'],
                entry['docdate'], entry['enriched'], entry['downloads'], entry['reads'])):
            payload['books'].append(entry)

    for row in bookshelf.objects.select_related('user', 'book'):
        entry = _book_key(digests, row.book)
        entry.update({
            'user': row.user.username,
            'status': row.status,
            'rating': row.rating,
            'percent': row.percent,
            'position': row.position,
            'readtime': _iso(row.readtime),
        })
        payload['shelves'].append(entry)

    for theme in Theme.objects.select_related('user'):
        payload['themes'].append({
            'user': theme.user.username,
            'theme_css': theme.theme_css,
            'reader_mode': theme.reader_mode,
            'font_size': theme.font_size,
        })

    try:
        from sopds_sync.models import KosyncProgress
        for row in KosyncProgress.objects.select_related('user'):
            payload['kosync'].append({
                'user': row.user.username,
                # Already a stable content hash computed by the client — the one
                # identifier here that needs no translation at all.
                'document': row.document,
                'progress': row.progress,
                'percentage': row.percentage,
                'device': row.device,
                'device_id': row.device_id,
                'timestamp': _iso(row.timestamp),
            })
    except Exception:      # pragma: no cover
        logger.exception('Could not export kosync progress')

    return payload


class Importer:
    """Restores an export, matching books and users by name rather than id."""

    def __init__(self, payload, force=False, dry_run=False):
        self.payload = payload
        self.force = force
        self.dry_run = dry_run
        self.stats = {'books': 0, 'shelves': 0, 'themes': 0, 'kosync': 0,
                      'unknown_books': 0, 'unknown_users': 0, 'skipped': 0}

        self._by_path = {(b.path, b.filename): b for b in Book.objects.all()}
        self._by_digest = {}
        for book_id, digest in _content_digests().items():
            self._by_digest[digest] = book_id
        self._books = {b.id: b for b in Book.objects.all()}
        self._users = {u.username: u for u in User.objects.all()}

    def find_book(self, entry):
        """The book this entry refers to in *this* catalogue, or None."""
        book = self._by_path.get((entry.get('path'), entry.get('filename')))
        if book is not None:
            return book

        # Renamed or moved since the export: fall back to content.
        digest = entry.get('digest')
        if digest:
            book_id = self._by_digest.get(digest)
            if book_id is not None:
                return self._books.get(book_id)
        return None

    def find_user(self, username):
        return self._users.get(username)

    def run(self):
        self._books_and_stats()
        self._shelves()
        self._themes()
        self._kosync()
        return self.stats

    def _books_and_stats(self):
        for entry in self.payload.get('books', []):
            book = self.find_book(entry)
            if book is None:
                self.stats['unknown_books'] += 1
                continue

            changed = {}
            for field in ('isbn', 'publisher', 'annotation', 'docdate'):
                value = entry.get(field)
                if value and (self.force or not getattr(book, field)):
                    changed[field] = value
            enriched = parse_datetime(entry.get('enriched') or '') if entry.get('enriched') else None
            if enriched and (self.force or book.enriched is None):
                changed['enriched'] = enriched

            if changed:
                self.stats['books'] += 1
                if not self.dry_run:
                    Book.objects.filter(pk=book.pk).update(**changed)

            downloads, reads = entry.get('downloads') or 0, entry.get('reads') or 0
            if (downloads or reads) and not self.dry_run:
                stat, _ = BookStat.objects.get_or_create(book=book)
                # Counters only ever go up: importing an old export into a live
                # catalogue must not erase what has been counted since.
                BookStat.objects.filter(book=book).update(
                    downloads=max(stat.downloads, downloads),
                    reads=max(stat.reads, reads))

    def _shelves(self):
        for entry in self.payload.get('shelves', []):
            user = self.find_user(entry.get('user'))
            if user is None:
                self.stats['unknown_users'] += 1
                continue
            book = self.find_book(entry)
            if book is None:
                self.stats['unknown_books'] += 1
                continue

            existing = bookshelf.objects.filter(user=user, book=book).first()
            if existing is not None and not self.force:
                self.stats['skipped'] += 1
                continue

            self.stats['shelves'] += 1
            if self.dry_run:
                continue

            readtime = parse_datetime(entry.get('readtime') or '') or timezone.now()
            bookshelf.objects.update_or_create(
                user=user, book=book,
                defaults={'status': entry.get('status') or '',
                          'rating': entry.get('rating'),
                          'percent': entry.get('percent'),
                          'position': entry.get('position'),
                          'readtime': readtime})

    def _themes(self):
        for entry in self.payload.get('themes', []):
            user = self.find_user(entry.get('user'))
            if user is None:
                self.stats['unknown_users'] += 1
                continue
            if Theme.objects.filter(user=user).exists() and not self.force:
                self.stats['skipped'] += 1
                continue

            self.stats['themes'] += 1
            if not self.dry_run:
                Theme.objects.update_or_create(
                    user=user,
                    defaults={'theme_css': entry.get('theme_css') or 'css/sopds.css',
                              'reader_mode': entry.get('reader_mode') or Theme.READER_WHOLE,
                              'font_size': entry.get('font_size') or 100})

    def _kosync(self):
        from sopds_sync.models import KosyncProgress

        for entry in self.payload.get('kosync', []):
            user = self.find_user(entry.get('user'))
            if user is None:
                self.stats['unknown_users'] += 1
                continue
            document = entry.get('document')
            if not document:
                continue
            if KosyncProgress.objects.filter(user=user, document=document).exists() \
                    and not self.force:
                self.stats['skipped'] += 1
                continue

            self.stats['kosync'] += 1
            if not self.dry_run:
                KosyncProgress.objects.update_or_create(
                    user=user, document=document,
                    defaults={'progress': entry.get('progress') or '',
                              'percentage': entry.get('percentage') or 0.0,
                              'device': entry.get('device') or '',
                              'device_id': entry.get('device_id') or '',
                              'timestamp': parse_datetime(entry.get('timestamp') or '')
                              or timezone.now()})


def load(payload, force=False, dry_run=False):
    version = payload.get('version')
    if version != VERSION:
        raise ValueError('unsupported export version %r (expected %d)' % (version, VERSION))
    return Importer(payload, force=force, dry_run=dry_run).run()
