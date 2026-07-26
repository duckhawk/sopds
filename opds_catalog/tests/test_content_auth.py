"""Every route that serves catalogue content honours SOPDS_AUTH.

The OPDS feeds and `Download` already answered 401 to an anonymous request, but
the cover, thumbnail, convert and — worst of the four — the reader route did
not: `/opds/read/<id>/` handed out the full text of any book to anybody who
knew an id.
"""
import base64
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


@pytest.fixture
def book(db):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    return Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en',
        title='The Sanctuary Sparrow', search_title='THE SANCTUARY SPARROW',
        annotation='', avail=2, catalog=cat,
    )


@pytest.fixture
def account(django_user_model):
    django_user_model.objects.create_user(username='reader', password='pw')
    return {'HTTP_AUTHORIZATION': 'Basic %s' % base64.b64encode(b'reader:pw').decode()}


def content_routes(book):
    return [
        ('cover', reverse('opds:cover', args=[book.id])),
        ('thumb', reverse('opds:thumb', args=[book.id])),
        ('read', reverse('opds:read', args=[book.id])),
        ('download', reverse('opds:download', args=[book.id, 0])),
        ('convert', reverse('opds:convert', args=[book.id, 'epub'])),
    ]


@pytest.mark.django_db
def test_anonymous_requests_are_refused(client, book):
    for name, url in content_routes(book):
        assert client.get(url).status_code == 401, '%s served an anonymous request' % name


@pytest.mark.django_db
def test_basic_auth_is_accepted(client, book, account):
    """E-readers cannot carry a session cookie; they re-send Basic auth."""
    for name in ('cover', 'thumb', 'read', 'download'):
        url = dict((n, u) for n, u in content_routes(book))[name]
        assert client.get(url, **account).status_code == 200, '%s rejected Basic auth' % name


@pytest.mark.django_db
def test_a_session_login_is_accepted(client, book, django_user_model):
    user = django_user_model.objects.create_user(username='web', password='pw')
    client.force_login(user)
    for name in ('cover', 'thumb', 'read', 'download'):
        url = dict((n, u) for n, u in content_routes(book))[name]
        assert client.get(url, **{}).status_code == 200, '%s rejected a session login' % name


@pytest.mark.django_db
def test_bad_credentials_are_refused(client, book):
    bad = {'HTTP_AUTHORIZATION': 'Basic %s' % base64.b64encode(b'reader:wrong').decode()}
    assert client.get(reverse('opds:cover', args=[book.id]), **bad).status_code == 401


@pytest.mark.django_db
def test_everything_is_open_when_auth_is_disabled(client, book):
    config.SOPDS_AUTH = False
    for name, url in content_routes(book):
        if name == 'convert':
            continue   # needs an external converter binary
        assert client.get(url).status_code == 200, '%s refused an open catalogue' % name


@pytest.mark.django_db
def test_the_401_comes_before_the_cover_is_extracted(client, book, monkeypatch):
    """Anonymous traffic must not be able to spend CPU unzipping and parsing
    books, nor to populate the cover cache."""
    from opds_catalog import dl

    def fail(*args, **kwargs):
        raise AssertionError('the book was opened for an unauthenticated request')

    monkeypatch.setattr(dl, 'extract_cover', fail)
    assert client.get(reverse('opds:cover', args=[book.id])).status_code == 401


@pytest.mark.django_db
def test_the_placeholder_stays_open(client):
    """The book-less no-cover image carries nothing from the catalogue, and
    templates use it as a plain default image."""
    config.SOPDS_AUTH = True
    assert client.get(reverse('opds:covertmpl')).status_code == 200
