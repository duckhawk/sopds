# -*- coding: utf-8 -*-
"""Two-way reading-position sync with Moon+ Reader over the WebDAV endpoint.

Moon+ Reader has no sync protocol: it treats the cloud folder as a file store
and leaves one position marker per book in it (:mod:`sopds_sync.moonreader`).
Both directions therefore come down to reading and writing those files at the
moments the rest of the app already has a position in its hands.

**In** — a marker the phone uploads is parsed as soon as it is stored, matched
to a catalogue book, and reflected onto the shelf: the percentage always, and
the browser reader's paragraph id whenever the marker's coordinates can be shown
to describe the copy we hold.

**Out** — a position saved in the browser reader is written back into the same
file the phone reads, so picking the book up on the phone continues from where
the browser left off.

The asymmetry between the two is deliberate and comes from the format. A
percentage means the same thing everywhere, so it is always safe to take in. A
chapter number does not: it counts entries in Moon+ Reader's table of contents
for the file on the device, and if that file is a different edition the number
points at a different place in the book. Reading one in is harmless — the worst
case is that we cannot use it — but writing one out would move the reader
somewhere they have never been. So nothing is written until an incoming marker
has demonstrated, by having its own percentage reproduced against our copy, that
the two files agree.

Nothing here may raise into its callers: a WebDAV PUT must store the file even
when we cannot make sense of it, and saving a position in the browser must not
fail because a phone is out of reach.
"""
import logging
import os

from django.utils import timezone

from constance import config

from opds_catalog.models import bookshelf

from . import linking, moonpos, moonreader
from .models import MoonReaderPosition

logger = logging.getLogger(__name__)


def _record(user, path, marker, book, rule):
    MoonReaderPosition.objects.update_or_create(
        user=user, path=path,
        defaults={
            'name': moonreader.book_name(path),
            'book': book,
            'marker': str(marker),
            'rule': rule or '',
            'updated': timezone.now(),
        })


def ingest(user, path, full_path):
    """Take in a position marker the device has just uploaded.

    `path` is the sub-path inside the user's DAV area, `full_path` the file on
    disk. Returns the shelf row that was updated, or None when nothing could be.
    """
    try:
        if not moonreader.is_position_file(path):
            return None

        with open(full_path, 'rb') as handle:
            marker = moonreader.parse(handle.read(256))
        if marker is None:
            logger.debug('Unparsable Moon+ marker at %s', path)
            return None

        name = moonreader.book_name(path)
        book = linking.resolve_book_by_name(name)
        if book is None:
            # Worth keeping even so: the marker is the user's data, and a book
            # added to the catalogue later can be matched on the next upload.
            _record(user, path, marker, None, '')
            logger.debug('No catalogue book for Moon+ file %r', name)
            return None

        outline = moonpos.fit(book, marker)
        _record(user, path, marker, book, outline.rule if outline else '')

        was = bookshelf.objects.filter(user=user, book=book).values_list(
            'percent', flat=True).first()
        shelf = linking.record_progress(user, book, marker.fraction)
        if shelf is None or outline is None:
            return shelf

        # Forward only, exactly as the percentage is: a second device reporting
        # a stale position must not drag the reading place backwards either, or
        # the two halves of the shelf row would start telling different stories.
        if was is not None and marker.fraction < was:
            return shelf

        # The coordinates check out against our copy, so they can be turned into
        # a place in the text the browser reader understands.
        paragraph = outline.resume_paragraph(marker.chapter, marker.offset)
        if paragraph and paragraph != shelf.position:
            shelf.position = paragraph
            shelf.save(update_fields=['position'])
        return shelf
    except Exception:
        logger.exception('Could not ingest the Moon+ Reader marker at %s', path)
        return None


def _dav_path(user, path):
    """Absolute path of a file in the user's DAV area, or None if disabled."""
    if not config.SOPDS_WEBDAV_ENABLE:
        return None
    root = os.path.abspath(os.path.join(config.SOPDS_WEBDAV_ROOT, str(user.id)))
    full = os.path.abspath(os.path.join(root, path))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def publish(user, book, paragraph_id):
    """Write a position picked in the browser reader back for the device.

    Only ever updates a marker file the device itself created, and only for a
    book whose chapter numbering an earlier upload confirmed: without that
    evidence the coordinates would be guesswork, and Moon+ Reader would act on
    them. Returns True if a file was written.
    """
    try:
        row = (MoonReaderPosition.objects
               .filter(user=user, book=book)
               .exclude(rule='')
               .order_by('-updated')
               .first())
        if row is None:
            return False

        previous = moonreader.parse(row.marker)
        if previous is None:
            return False

        # The rule an upload confirmed for this book, taken from the row rather
        # than from a cache: the evidence that the phone's copy is ours has to
        # outlive an eviction, or write-back would quietly stop working.
        outline = moonpos.for_rule(book, row.rule)
        if outline is None:
            return False

        char_offset = outline.char_at(paragraph_id)
        if char_offset is None:
            return False

        # The phone counts characters, the browser reader counts paragraphs, so
        # a position that came from the phone maps back to the *start* of the
        # paragraph it was inside. Writing that back would rewind the device by
        # up to a paragraph every time the two are compared, and keep doing it.
        # Only a genuinely different paragraph is worth a write.
        if outline.resume_paragraph(previous.chapter, previous.offset) == paragraph_id:
            return False

        chapter, offset, percent = outline.locate(char_offset)
        marker = previous.replace(chapter=chapter, offset=offset, percent=percent)

        full = _dav_path(user, row.path)
        if full is None or not os.path.isdir(os.path.dirname(full)):
            # Only ever overwrite in a directory the device made. Creating one
            # would leave a marker for a book Moon+ Reader may not even have.
            return False

        with open(full, 'w', encoding='utf-8') as handle:
            handle.write(str(marker))

        row.marker = str(marker)
        row.updated = timezone.now()
        row.save(update_fields=['marker', 'updated'])
        return True
    except Exception:
        logger.exception('Could not publish a Moon+ Reader marker for book %s',
                         getattr(book, 'id', None))
        return False
