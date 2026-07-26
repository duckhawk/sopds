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

    The `pk__in` is what keeps this affordable, and it is not redundant with the
    ordering below. Aggregating straight over `Book` makes the join and the
    GROUP BY cover the whole catalogue and then discards almost all of it with a
    HAVING — cost grows with the number of books, not with the number of
    ratings. Measured on sqlite with 200 rated books, that was 4 ms at 2k books
    and 66 ms at 60k, for a result that never changed. Restricting the outer
    query to the books someone has actually rated makes it grow with the rated
    set instead, which is bounded by how much people rate rather than by how
    large the library is.
    """
    rated = (bookshelf.objects
             .filter(rating__isnull=False)
             .values_list('book', flat=True)
             .distinct())

    return (Book.objects
            .filter(pk__in=rated)
            .annotate(rating_average=Avg('bookshelf__rating'),
                      rating_votes=Count('bookshelf__rating'))
            .order_by('-rating_average', '-rating_votes', 'search_title'))
