"""Narrowing the bookshelf by reading status.

Status is set by hand and, since #71, by an e-reader syncing progress — but the
shelf could only ever be shown whole.
"""
import re

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog, bookshelf


@pytest.fixture
def shelf(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    user = django_user_model.objects.create_user(username='reader', password='pw')

    def add(title, status):
        book = Book.objects.create(
            filename='%s.fb2' % title, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title=title, search_title=title.upper(),
            annotation='', avail=2, catalog=cat)
        bookshelf.objects.create(user=user, book=book, status=status)
        return book

    books = {
        'Queued book': add('Queued book', bookshelf.STATUS_TO_READ),
        'Current book': add('Current book', bookshelf.STATUS_READING),
        'Finished book': add('Finished book', bookshelf.STATUS_READ),
        'Unmarked book': add('Unmarked book', ''),
    }
    client.force_login(user)
    return books


def shelf_body(client, **params):
    params.setdefault('searchtype', 'u')
    return client.get(reverse('web:searchbooks'), params).content.decode()


def listed(client, **params):
    """Titles in the result list.

    Not a plain substring check on the page: the sidebar shows the last books
    read regardless of any filter, so a title can appear there while being
    correctly absent from the results.
    """
    body = shelf_body(client, **params)
    return set(re.findall(r'<b id="\d+">([^<]+)</b>', body))


@pytest.mark.django_db
def test_the_whole_shelf_is_shown_by_default(client, shelf):
    assert listed(client) == set(shelf)


@pytest.mark.django_db
@pytest.mark.parametrize('status, kept', [
    ('to_read', 'Queued book'),
    ('reading', 'Current book'),
    ('read', 'Finished book'),
])
def test_filtering_keeps_only_that_status(client, shelf, status, kept):
    assert listed(client, status=status) == {kept}


@pytest.mark.django_db
def test_an_unknown_status_shows_the_whole_shelf(client, shelf):
    """Better than an empty shelf with no explanation."""
    assert listed(client, status='sideways') == set(shelf)


@pytest.mark.django_db
def test_the_filter_survives_pagination(client, shelf, django_user_model):
    """Page two used to drop the filter and quietly show everything."""
    user = django_user_model.objects.get(username='reader')
    cat = Catalog.objects.get()
    for n in range(3):
        extra = Book.objects.create(
            filename='more%d.fb2' % n, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title='More reading %d' % n,
            search_title='MORE READING %d' % n, annotation='', avail=2, catalog=cat)
        bookshelf.objects.create(user=user, book=extra, status=bookshelf.STATUS_READING)

    config.SOPDS_MAXITEMS = 2
    body = shelf_body(client, status='reading')
    assert 'status=reading&page=2' in body

    # And page two really is still filtered.
    assert listed(client, status='reading', page=2) and \
        'Queued book' not in listed(client, status='reading', page=2)


@pytest.mark.django_db
def test_the_filter_links_are_offered_on_the_shelf(client, shelf):
    body = shelf_body(client)
    for status in ('to_read', 'reading', 'read'):
        assert 'searchtype=u&status=%s' % status in body


@pytest.mark.django_db
def test_the_filter_is_scoped_to_this_user(client, shelf, django_user_model):
    """Another reader's 'reading' must not leak into mine."""
    other = django_user_model.objects.create_user(username='other', password='pw')
    mine = Book.objects.get(title='Queued book')
    bookshelf.objects.create(user=other, book=mine, status=bookshelf.STATUS_READING)

    assert listed(client, status='reading') == {'Current book'}
