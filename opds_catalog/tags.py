# -*- coding: utf-8 -*-
"""Free-form labels on books.

Genres arrive with the files and follow a taxonomy nobody in this library
chose. Tags are the other half: whatever its readers find worth marking, which
no parser will ever produce.

Names are matched case-insensitively so "Book club" and "book club" are one tag
rather than two — the commonest way a shared vocabulary quietly falls apart.
The first spelling used is the one kept and displayed.
"""
from django.db.models import Count

from opds_catalog.models import SIZE_TAG, Book, Tag, btag

# A tag is a label, not a note. The cap keeps the browse list readable and stops
# someone pasting a paragraph into the field.
MAX_PER_BOOK = 30


def normalize(name):
    """A tag name as it will be stored, or '' if it is not usable."""
    return ' '.join((name or '').split())[:SIZE_TAG].strip()


def get_or_create(name):
    """The tag with this name, matching case-insensitively. None if unusable."""
    name = normalize(name)
    if not name:
        return None

    existing = Tag.objects.filter(search_name=name.upper()).first()
    if existing is not None:
        return existing

    return Tag.objects.create(name=name, search_name=name.upper())


def add(book, name):
    """Tag a book. Returns the tag, or None if the name or the count refuses."""
    tag = get_or_create(name)
    if tag is None:
        return None
    if btag.objects.filter(book=book).count() >= MAX_PER_BOOK:
        return None

    btag.objects.get_or_create(book=book, tag=tag)
    return tag


def remove(book, tag_id):
    """Untag a book, and drop the tag itself once nothing carries it.

    Without the second half the browse list fills up with labels that match
    nothing, and there is nowhere to delete them from.
    """
    deleted, _ = btag.objects.filter(book=book, tag_id=tag_id).delete()
    if deleted:
        Tag.objects.filter(pk=tag_id, btag__isnull=True).delete()
    return bool(deleted)


def for_books(book_ids):
    """`{book_id: [tag, ...]}` for a page of books, in one query."""
    book_ids = list(book_ids)
    if not book_ids:
        return {}

    result = {}
    for row in btag.objects.filter(book_id__in=book_ids).select_related('tag'):
        result.setdefault(row.book_id, []).append(row.tag)
    return result


def in_use():
    """Every tag that is on at least one book, with how many carry it."""
    return (Tag.objects
            .annotate(book_count=Count('btag'))
            .filter(book_count__gt=0)
            .order_by('search_name'))


def books_with(tag_id):
    return Book.objects.filter(tags=tag_id).order_by('search_title', '-docdate')
