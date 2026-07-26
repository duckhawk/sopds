"""The rate limit on the routes that hand out book content.

Only the login form was throttled. That mattered less when every content route
was a file read, and matters more now that one of them unzips a book and parses
every document in its spine.
"""
import os

import pytest
from django.core.cache import cache
from django.urls import reverse
from constance import config

from opds_catalog import throttle
from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def book(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_RATE_LIMIT = 3
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    book = Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en', title='Sparrow',
        search_title='SPARROW', annotation='', avail=2, catalog=cat)
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    return book


def cover(client, book):
    return client.get(reverse('opds:cover', args=[book.id]))


# --- enforcement -----------------------------------------------------------

@pytest.mark.django_db
def test_requests_within_the_limit_are_served(client, book):
    for _ in range(3):
        assert cover(client, book).status_code == 200


@pytest.mark.django_db
def test_going_over_the_limit_is_429_with_retry_after(client, book):
    for _ in range(3):
        cover(client, book)

    resp = cover(client, book)
    assert resp.status_code == 429
    assert resp['Retry-After'] == '60'


@pytest.mark.django_db
@pytest.mark.parametrize('name, args', [
    ('opds:cover', None), ('opds:thumb', None), ('opds:read', None),
])
def test_every_content_route_is_covered(client, book, name, args):
    url = reverse(name, args=[book.id])
    for _ in range(3):
        client.get(url)
    assert client.get(url).status_code == 429


@pytest.mark.django_db
def test_download_is_covered_too(client, book):
    url = reverse('opds:download', args=[book.id, 0])
    for _ in range(3):
        client.get(url)
    assert client.get(url).status_code == 429


@pytest.mark.django_db
def test_the_budget_is_shared_across_routes(client, book):
    """It is a limit on a client, not on a URL."""
    cover(client, book)
    client.get(reverse('opds:thumb', args=[book.id]))
    client.get(reverse('opds:download', args=[book.id, 0]))
    assert cover(client, book).status_code == 429


# --- who is counted --------------------------------------------------------

@pytest.mark.django_db
def test_readers_are_counted_separately(client, book, django_user_model):
    """A household behind one address is several readers."""
    for _ in range(4):
        cover(client, book)
    assert cover(client, book).status_code == 429

    other = django_user_model.objects.create_user(username='other', password='pw')
    client.force_login(other)
    assert cover(client, book).status_code == 200


@pytest.mark.django_db
def test_anonymous_clients_are_counted_by_address(client, book, rf):
    config.SOPDS_AUTH = False
    request = rf.get('/', REMOTE_ADDR='10.0.0.7')
    request.user = None
    assert throttle.client_id(request) == 'i10.0.0.7'


@pytest.mark.django_db
def test_a_forwarded_address_is_preferred_behind_a_proxy(rf):
    request = rf.get('/', REMOTE_ADDR='127.0.0.1',
                     HTTP_X_FORWARDED_FOR='203.0.113.9, 10.0.0.1')
    request.user = None
    assert throttle.client_id(request) == 'i203.0.113.9'


# --- configuration and failure modes ---------------------------------------

@pytest.mark.django_db
def test_zero_disables_the_limit(client, book):
    config.SOPDS_RATE_LIMIT = 0
    for _ in range(20):
        assert cover(client, book).status_code == 200


@pytest.mark.django_db
def test_a_broken_cache_lets_the_request_through(rf, monkeypatch):
    """An unenforced limit is a much smaller problem than a refused library.

    Checked against `over_limit` rather than through a view: the rest of the
    request also uses the cache, and hardening every one of those against a
    dead Redis is a separate question from what this module promises.
    """
    def boom(*args, **kwargs):
        raise RuntimeError('redis is gone')

    monkeypatch.setattr(cache, 'incr', boom)
    monkeypatch.setattr(cache, 'set', boom)

    request = rf.get('/', REMOTE_ADDR='10.0.0.7')
    request.user = None
    assert throttle.over_limit(request) is False


@pytest.mark.django_db
def test_the_limit_is_checked_before_anything_is_spent(client, book, monkeypatch):
    """A client over its budget must not be able to make us unzip a book."""
    from opds_catalog import dl

    for _ in range(3):
        cover(client, book)

    def fail(*args, **kwargs):
        raise AssertionError('the book was opened for a throttled request')

    monkeypatch.setattr(dl, 'extract_cover', fail)
    assert cover(client, book).status_code == 429


@pytest.mark.django_db
def test_the_feeds_are_not_throttled(client, book):
    """Browsing is cheap; it is handing out content that is not."""
    config.SOPDS_RATE_LIMIT = 1
    cover(client, book)
    for _ in range(5):
        assert client.get(reverse('opds:main')).status_code == 200
