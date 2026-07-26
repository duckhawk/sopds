# -*- coding: utf-8 -*-
"""OPDS 2.0 — the JSON catalogue format.

The existing feeds are OPDS 1.2 Atom, which every e-reader still speaks. The
newer clients — Thorium, Foliate, recent Aldiko — prefer 2.0, and some only
offer it. This serves the same catalogue in that format alongside the Atom one
rather than instead of it; nothing about the 1.2 feeds changes.

The shape here was taken from the official test catalogue at
https://test.opds.io/2.0/home.json rather than from the specification prose,
because the published draft at drafts.opds.io/opds-2.0 currently 404s and a
live reference implementation is a better thing to match anyway. A feed is:

    {"metadata": {...}, "links": [...], "navigation": [...], "publications": [...]}

where a publication carries `metadata`, acquisition `links` and `images`.
"""
from django.urls import reverse

from opds_catalog import models, settings
from opds_catalog.models import Counter
from book_tools.format import mime_detector
from book_tools.format.mimetype import Mimetype

CONTENT_TYPE = 'application/opds+json'

# Relations, spelled out once. These are the OPDS 1.x URIs, which 2.0 keeps.
ACQUISITION = 'http://opds-spec.org/acquisition/open-access'
IMAGE = 'http://opds-spec.org/image'
THUMBNAIL = 'http://opds-spec.org/image/thumbnail'


def absolute(request, path):
    """OPDS 2.0 clients are entitled to absolute URLs, and several insist."""
    return request.build_absolute_uri(path)


def publication(request, book, authors=None, series=None):
    """One book, as an OPDS 2.0 Publication.

    `authors`/`series` may be passed in when the caller has already prefetched
    them, so rendering a page does not become one query per book.
    """
    authors = book.authors.all() if authors is None else authors
    series = book.series.all() if series is None else series

    metadata = {
        '@type': 'http://schema.org/Book',
        'title': book.title,
        # A stable, resolvable-shaped identifier. The ISBN when we know it,
        # since that is the one identifier a client might recognise; otherwise
        # a URN naming this catalogue, which is at least unique here.
        'identifier': ('urn:isbn:%s' % book.isbn) if book.isbn
                      else 'urn:lectern:book:%s' % book.id,
        'modified': book.registerdate.isoformat(),
    }
    if book.lang:
        metadata['language'] = book.lang
    if book.docdate:
        metadata['published'] = book.docdate
    if book.publisher:
        metadata['publisher'] = book.publisher
    if book.annotation:
        metadata['description'] = book.annotation

    names = [a.full_name for a in authors]
    if names:
        # One author is an object, several are a list — both are allowed, and
        # clients handle the singular form more reliably.
        metadata['author'] = ({'name': names[0]} if len(names) == 1
                              else [{'name': n} for n in names])
    if series:
        metadata['belongsTo'] = {'series': [{'name': s.ser} for s in series]}

    mime = str(mime_detector.fmt(book.format))
    links = [{
        'rel': ACQUISITION,
        'href': absolute(request, reverse('opds_catalog:download',
                                          kwargs={'book_id': book.id, 'zip_flag': 0})),
        'type': mime,
    }]
    if book.format not in settings.NOZIP_FORMATS:
        links.append({
            'rel': ACQUISITION,
            'href': absolute(request, reverse('opds_catalog:download',
                                              kwargs={'book_id': book.id, 'zip_flag': 1})),
            'type': Mimetype.FB2_ZIP if mime == Mimetype.FB2 else '%s+zip' % mime,
        })

    return {
        'metadata': metadata,
        'links': links,
        'images': [
            {'rel': THUMBNAIL,
             'href': absolute(request, reverse('opds_catalog:thumb',
                                               kwargs={'book_id': book.id})),
             'type': 'image/jpeg'},
            {'rel': IMAGE,
             'href': absolute(request, reverse('opds_catalog:cover',
                                               kwargs={'book_id': book.id})),
             'type': 'image/jpeg'},
        ],
    }


def navigation_feed(request, title, entries):
    """A feed whose items are places to go rather than books."""
    return {
        'metadata': {'title': title},
        'links': [{'rel': 'self', 'href': absolute(request, request.path),
                   'type': CONTENT_TYPE},
                  search_link(request)],
        'navigation': entries,
    }


def search_link(request):
    """A templated search link, as 2.0 expects rather than an OpenSearch document."""
    return {
        'rel': 'search',
        'href': '%s{?query}' % absolute(request, reverse('opds_catalog:opds2_search')),
        'type': CONTENT_TYPE,
        'templated': True,
    }


def publication_feed(request, title, books, page, per_page, total):
    """A feed of books, with the pagination 2.0 expresses in metadata."""
    metadata = {
        'title': title,
        'numberOfItems': total,
        'itemsPerPage': per_page,
        'currentPage': page,
    }

    def page_url(number):
        query = request.GET.copy()
        query['page'] = number
        return '%s?%s' % (absolute(request, request.path), query.urlencode())

    links = [{'rel': 'self', 'href': absolute(request, request.get_full_path()),
              'type': CONTENT_TYPE},
             search_link(request)]
    if page > 1:
        links.append({'rel': 'previous', 'href': page_url(page - 1), 'type': CONTENT_TYPE})
    if page * per_page < total:
        links.append({'rel': 'next', 'href': page_url(page + 1), 'type': CONTENT_TYPE})

    return {
        'metadata': metadata,
        'links': links,
        'publications': [publication(request, b, list(b.authors.all()), list(b.series.all()))
                         for b in books],
    }


def root_entries(request):
    """The same entry points the Atom root feed offers."""
    counter = Counter.objects

    def entry(url_name, title, count=None):
        item = {'href': absolute(request, reverse(url_name)),
                'title': title, 'type': CONTENT_TYPE}
        if count is not None:
            item['metadata'] = {'numberOfItems': count}
        return item

    books = counter.get_counter(models.counter_allbooks)
    return [
        entry('opds_catalog:opds2_new', 'Recently added', books),
        entry('opds_catalog:opds2_rated', 'Top rated'),
        entry('opds_catalog:opds2_popular', 'Most popular'),
        entry('opds_catalog:opds2_books', 'All books', books),
    ]
