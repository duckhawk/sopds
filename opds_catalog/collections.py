# -*- coding: utf-8 -*-
"""Named lists of books that belong to a reader.

The three grouping mechanisms in this catalogue answer different questions and
are deliberately not merged. The bookshelf is an automatic record of what you
have opened. Tags are shared metadata about the book. A collection is a
deliberate grouping that means something only to whoever made it.
"""
from django.db.models import BooleanField, Case, Count, Q, Value, When

from opds_catalog.models import SIZE_COLLECTION, Book, Collection, CollectionBook

# A reader with hundreds of lists has not organised anything, and the picker on
# the book card stops being usable long before that.
MAX_PER_USER = 100


def normalize(name):
    return ' '.join((name or '').split())[:SIZE_COLLECTION].strip()


def create(user, name, shared=False):
    """A new list for this reader. None if the name or the count refuses."""
    name = normalize(name)
    if not name:
        return None
    if Collection.objects.filter(user=user).count() >= MAX_PER_USER:
        return None

    collection, _created = Collection.objects.get_or_create(
        user=user, name=name, defaults={'shared': shared})
    return collection


def visible(user):
    """Every list this reader may look at: their own, plus anyone's shared ones.

    Ordered so a reader's own lists come first — they are what the picker on a
    book card is for, and what they are most likely to want.
    """
    if not user.is_authenticated:
        return (Collection.objects.filter(shared=True)
                .select_related('user')
                .annotate(book_count=Count('collectionbook'))
                .order_by('name'))

    return (Collection.objects
            .filter(Q(user=user) | Q(shared=True))
            .select_related('user')
            .annotate(book_count=Count('collectionbook'),
                      mine=Case(When(user=user, then=Value(True)),
                                default=Value(False), output_field=BooleanField()))
            .order_by('-mine', 'name'))


def owned(user):
    if not user.is_authenticated:
        return Collection.objects.none()
    return (Collection.objects.filter(user=user)
            .annotate(book_count=Count('collectionbook')).order_by('name'))


def add_book(collection, book):
    _row, created = CollectionBook.objects.get_or_create(collection=collection, book=book)
    return created


def remove_book(collection, book):
    deleted, _ = CollectionBook.objects.filter(collection=collection, book=book).delete()
    return bool(deleted)


def books_in(collection):
    """The books, in the order they were added."""
    return (Book.objects
            .filter(collectionbook__collection=collection)
            .order_by('collectionbook__added', 'collectionbook__id'))


def containing(user, book_ids):
    """`{book_id: [collection_id, ...]}` for this reader, over a page of books.

    Lets the card show which of your lists a book is already on without a query
    per book.
    """
    book_ids = list(book_ids)
    if not book_ids or not user.is_authenticated:
        return {}

    result = {}
    rows = CollectionBook.objects.filter(
        collection__user=user, book_id__in=book_ids).values_list('book_id', 'collection_id')
    for book_id, collection_id in rows:
        result.setdefault(book_id, []).append(collection_id)
    return result
