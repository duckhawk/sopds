# -*- coding: utf-8 -*-
"""Views for the OPDS 2.0 feeds.

Thin: each one picks a queryset — the same ones the Atom feeds and the web
listings use, so the two formats can never disagree about what is in the
catalogue — and hands it to `opds2` to render.
"""
from django.http import JsonResponse

from constance import config

from opds_catalog import dl, opds2, ratings, stats
from opds_catalog.models import Book

MAX_PER_PAGE = 100


def _page(request):
    """The requested page number, clamped to something sane."""
    try:
        page = int(request.GET.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    return max(1, page)


def _per_page():
    # Shares SOPDS_MAXITEMS with the Atom feeds, capped so a client cannot ask
    # the server to render the whole catalogue in one response.
    return max(1, min(config.SOPDS_MAXITEMS, MAX_PER_PAGE))


def _json(payload):
    # json_dumps_params: the catalogue is full of cyrillic, and escaping it to
    # \uXXXX triples the size of a feed for no benefit to any client.
    return JsonResponse(payload, content_type=opds2.CONTENT_TYPE,
                        json_dumps_params={'ensure_ascii': False})


def _publications(request, title, queryset):
    page, per_page = _page(request), _per_page()
    total = queryset.count()
    start = (page - 1) * per_page
    books = list(queryset.prefetch_related('authors', 'series')[start:start + per_page])
    return _json(opds2.publication_feed(request, title, books, page, per_page, total))


@dl.require_login
def root(request):
    return _json(opds2.navigation_feed(request, opds2.settings.TITLE,
                                       opds2.root_entries(request)))


@dl.require_login
def new_books(request):
    return _publications(request, 'Recently added',
                         Book.objects.all().order_by('-registerdate', '-id'))


@dl.require_login
def top_rated(request):
    return _publications(request, 'Top rated', ratings.top_rated())


@dl.require_login
def popular(request):
    return _publications(request, 'Most popular', stats.most_popular())


@dl.require_login
def all_books(request):
    return _publications(request, 'All books',
                         Book.objects.all().order_by('search_title', '-docdate'))


@dl.require_login
def search(request):
    """`?query=` — the target of the templated search link in every feed."""
    term = (request.GET.get('query') or '').strip()
    if not term:
        # An empty search is not an error, it just has no results; a client
        # following the template before the reader typed anything gets this.
        return _publications(request, 'Search', Book.objects.none())

    books = (Book.objects.filter(search_title__contains=term.upper())
             .order_by('search_title', '-docdate'))
    return _publications(request, 'Search: %s' % term, books)
