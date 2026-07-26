"""A cache outage must degrade the catalogue, not take it down.

Django's RedisCache lets connection errors out, so with Redis unreachable every
`cache.get` in a view raised and the page 500'd — for something that exists
purely to make requests faster.
"""
import os

import pytest
from django.core.cache.backends.redis import RedisCache
from django.test import override_settings
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog
from sopds.cache import ResilientRedisCache, _redis_errors

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


# OSError, not a synthetic exception: it is what a socket failure actually
# raises, it is in the caught tuple whether or not redis-py is importable, and
# using the real thing means the test exercises the same path production will.
def failing(*args, **kwargs):
    raise OSError('connection refused')


@pytest.fixture
def backend():
    """A resilient backend without a real Redis behind it."""
    instance = ResilientRedisCache.__new__(ResilientRedisCache)
    instance._errors = _redis_errors()
    return instance


@pytest.fixture
def dead(backend, monkeypatch):
    for name in ('get', 'get_many', 'set', 'set_many', 'add', 'touch',
                 'delete', 'delete_many', 'has_key', 'incr', 'clear'):
        monkeypatch.setattr(RedisCache, name, failing)
    return backend


# --- the backend contract --------------------------------------------------

def test_a_read_misses_rather_than_raising(dead):
    assert dead.get('k') is None
    assert dead.get('k', 'fallback') == 'fallback'
    assert dead.get_many(['a', 'b']) == {}


def test_a_write_vanishes_rather_than_raising(dead):
    assert dead.set('k', 1) is False
    assert dead.add('k', 1) is False
    assert dead.touch('k') is False
    assert dead.set_many({'a': 1, 'b': 2}) == ['a', 'b']


def test_deletes_and_probes_do_not_raise(dead):
    assert dead.delete('k') is False
    assert dead.delete_many(['a']) is None
    assert dead.has_key('k') is False
    assert dead.clear() is None


def test_incr_raises_the_missing_key_error(dead):
    """Which is what both counters already handle: it is how they seed a
    window, so a dead cache makes every request look like the first."""
    with pytest.raises(ValueError):
        dead.incr('k')


def test_an_unrelated_error_is_not_swallowed(backend, monkeypatch):
    """Only "the cache is not answering" is survivable; a bug is not."""
    def bug(*args, **kwargs):
        raise TypeError('programming error')

    monkeypatch.setattr(RedisCache, 'get', bug)
    with pytest.raises(TypeError):
        backend.get('k')


def test_a_working_backend_is_left_alone(backend, monkeypatch):
    monkeypatch.setattr(RedisCache, 'get', lambda self, k, d=None, v=None: 'value')
    monkeypatch.setattr(RedisCache, 'set', lambda self, k, val, t=None, v=None, **kw: True)
    assert backend.get('k') == 'value'
    assert backend.set('k', 1) is True


# --- what the views do -----------------------------------------------------

@pytest.fixture
def book(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_RATE_LIMIT = 0
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    book = Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en', title='Sparrow',
        search_title='SPARROW', annotation='', avail=2, catalog=cat)
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    return book


# The resilience lives in the Redis backend subclass, so exercising it through a
# view means actually configuring that backend — patching the LocMem cache the
# tests otherwise use would prove nothing about production.
resilient_cache = override_settings(CACHES={'default': {
    'BACKEND': 'sopds.cache.ResilientRedisCache',
    'LOCATION': 'redis://127.0.0.1:6379',   # never connected to; see `broken_cache`
}})


@pytest.fixture
def broken_cache(monkeypatch):
    """Configure the resilient backend and make the Redis under it unreachable."""
    for name in ('get', 'get_many', 'set', 'set_many', 'add', 'touch',
                 'delete', 'delete_many', 'has_key', 'incr', 'clear'):
        monkeypatch.setattr(RedisCache, name, failing)
    with resilient_cache:
        yield


@pytest.mark.django_db
def test_a_cover_is_still_served_without_a_cache(client, book, broken_cache):
    """It was a 500 before: the throttle in front handled a dead cache and the
    cover view behind it did not."""
    resp = client.get(reverse('opds:cover', args=[book.id]))
    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/jpeg'


@pytest.mark.django_db
def test_a_page_is_still_served_without_a_cache(client, book, broken_cache):
    assert client.get(reverse('web:main')).status_code == 200


@pytest.mark.django_db
def test_a_download_is_still_served_without_a_cache(client, book, broken_cache):
    assert client.get(reverse('opds:download', args=[book.id, 0])).status_code == 200


@pytest.mark.django_db
def test_login_still_works_without_a_cache(client, book, broken_cache):
    """The lockout counter lives in the cache; losing it must not lock the door
    instead of merely failing to bolt it."""
    client.logout()
    resp = client.post(reverse('web:login'), {'username': 'r', 'password': 'pw'})
    assert resp.status_code == 302


@pytest.mark.django_db
def test_a_wrong_password_still_gets_the_normal_refusal(client, book, broken_cache):
    """403, the same as always — not a 500 from the throttle behind it."""
    client.logout()
    resp = client.post(reverse('web:login'), {'username': 'r', 'password': 'wrong'})
    assert resp.status_code == 403


@pytest.mark.django_db
def test_the_lockout_is_simply_not_enforced_while_the_cache_is_down(client, book, broken_cache):
    """Stated because it is a real consequence, not an accident: a cache outage
    is also a window with no brute-force protection on the login form."""
    client.logout()
    for _ in range(15):        # well past LOGIN_RATE_LIMIT
        client.post(reverse('web:login'), {'username': 'r', 'password': 'wrong'})

    resp = client.post(reverse('web:login'), {'username': 'r', 'password': 'pw'})
    assert resp.status_code == 302


# --- visibility ------------------------------------------------------------

@pytest.mark.django_db
def test_metrics_report_the_cache_as_down(client, broken_cache):
    """A degraded cache does not stop the catalogue serving, so nothing else
    would report it."""
    config.SOPDS_METRICS_ENABLE = True
    config.SOPDS_METRICS_TOKEN = ''
    body = client.get('/metrics').content.decode()
    assert 'lectern_cache_up 0' in body


@pytest.mark.django_db
def test_metrics_report_a_healthy_cache(client, db):
    config.SOPDS_METRICS_ENABLE = True
    config.SOPDS_METRICS_TOKEN = ''
    body = client.get('/metrics').content.decode()
    assert 'lectern_cache_up 1' in body


def test_the_caught_errors_always_include_socket_failures():
    """redis-py may not be importable (it is only needed where it is used), and
    the backend must still catch the errors a dead socket produces."""
    assert OSError in _redis_errors()
