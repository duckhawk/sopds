# -*- coding: utf-8 -*-
"""Community ratings, aggregated across users.

`bookshelf.rating` has been collected per user since #15, but only ever read
back to redraw that same user's own stars — nobody could see what the library as
a whole thought of a book, and nothing could be ordered by it.

The aggregate is deliberately computed for one page of books at a time rather
than annotated onto the main queryset. The book list is assembled three
different ways (the trigram fast path for title search, plain slicing, and the
id-driven path), and its pagination already leans on the row order; adding a
GROUP BY to each of those would change their plans for no gain. One extra query
per page keyed on the ids it already has is both cheaper and harder to break.
"""
from django.db.models import Avg, Count

from opds_catalog.models import Book, bookshelf


def summary(book_ids):
    """`{book_id: {'average': float, 'votes': int}}` for the rated books given.

    Books nobody has rated are absent from the mapping rather than present with
    a zero, so a caller can tell "unrated" from "rated badly".
    """
    book_ids = list(book_ids)
    if not book_ids:
        return {}

    rows = (bookshelf.objects
            .filter(book_id__in=book_ids, rating__isnull=False)
            .values('book_id')
            .annotate(average=Avg('rating'), votes=Count('rating')))

    return {r['book_id']: {'average': round(r['average'], 1), 'votes': r['votes']}
            for r in rows}


def top_rated():
    """Books ordered by community rating, best first.

    Unrated books are excluded: they would otherwise fill the list with nulls.
    Votes break a tie on the average, so a book five people rated 5 outranks one
    a single person did, and the title breaks the remaining ties to keep paging
    stable.
    """
    return (Book.objects
            .annotate(rating_average=Avg('bookshelf__rating'),
                      rating_votes=Count('bookshelf__rating'))
            .filter(rating_votes__gt=0)
            .order_by('-rating_average', '-rating_votes', 'search_title'))
