"""Browsing the web UI with authentication turned off.

With `SOPDS_AUTH = False` every visitor is an `AnonymousUser`, and `sopds_login`
lets them through on purpose. Every page then called `theme_css(request.user)`,
which filtered `Theme` by that AnonymousUser — `TypeError: Field 'id' expected a
number` — so the whole UI answered 500.
"""
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'opds_catalog', 'tests', 'data')
FB2 = '262001.fb2'


@pytest.fixture
def anon(db):
    config.SOPDS_AUTH = False
    config.SOPDS_ROOT_LIB = DATA
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    return Book.objects.create(
        filename=FB2, path='.', filesize=1, format='fb2', cat_type=0,
        docdate='2011', lang='en', title='The Sanctuary Sparrow',
        search_title='THE SANCTUARY SPARROW', annotation='', avail=2, catalog=cat,
    )


BROWSING_PAGES = [
    ('web:main', {}, {}),
    ('web:catalog', {}, {}),
    ('web:book', {}, {'lang': 0}),
    ('web:author', {}, {'lang': 0}),
    ('web:series', {}, {'lang': 0}),
    ('web:genre', {}, {}),
    ('web:searchbooks', {}, {'searchtype': 'm', 'searchterms': 'Sanctuary'}),
    ('web:searchauthors', {}, {'searchtype': 'm', 'searchterms': 'Peters'}),
    ('web:searchseries', {}, {'searchtype': 'm', 'searchterms': 'Cadfael'}),
]


@pytest.mark.django_db
@pytest.mark.parametrize('name, args, query', BROWSING_PAGES)
def test_browsing_pages_serve_anonymous_visitors(client, anon, name, args, query):
    resp = client.get(reverse(name, kwargs=args), query)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_reader_renders_for_an_anonymous_visitor(client, anon):
    resp = client.get(reverse('web:read', args=[anon.id]))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_suggestions_work_for_an_anonymous_visitor(client, anon):
    resp = client.post(reverse('web:suggest'), {'searchterms': 'Sanc', 'suggesttype': 'title'})
    assert resp.status_code == 200


PERSONAL_GET_VIEWS = [
    ('web:settings', {}),
    ('web:devicesync', {}),
    ('web:theme', {}),
    ('web:bsadd', {}),
    ('web:bsclear', {}),
]


@pytest.mark.django_db
@pytest.mark.parametrize('name, args', PERSONAL_GET_VIEWS)
def test_personal_pages_are_forbidden_not_broken(client, anon, name, args):
    """These act on one user's rows; with nobody signed in there is no such user.
    They must say so, not raise TypeError."""
    assert client.get(reverse(name, kwargs=args)).status_code == 403


@pytest.mark.django_db
def test_personal_book_endpoints_are_forbidden(client, anon):
    assert client.get(reverse('web:getpos', args=[anon.id])).status_code == 403
    assert client.get(reverse('web:setpos', args=[anon.id]), {'pos': '1.2'}).status_code == 403
    assert client.post(reverse('web:bsstatus', args=[anon.id]), {'status': 'read'}).status_code == 403
    assert client.post(reverse('web:bsrating', args=[anon.id]), {'rating': '4'}).status_code == 403
    assert client.delete('%s?book=%s' % (reverse('web:bsdel'), anon.id)).status_code == 403


@pytest.mark.django_db
def test_personal_pages_still_work_when_signed_in(client, anon, django_user_model):
    config.SOPDS_AUTH = True
    user = django_user_model.objects.create_user(username='reader', password='pw')
    client.force_login(user)
    assert client.get(reverse('web:settings')).status_code == 200
    assert client.post(reverse('web:bsrating', args=[anon.id]), {'rating': '4'}).status_code == 200
