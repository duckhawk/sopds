"""Conditional-GET and caching behaviour of the cover/thumbnail views."""
import os
import shutil

import pytest
from django.core.cache import cache
from django.urls import reverse
from constance import config

from opds_catalog import dl
from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


@pytest.fixture(autouse=True)
def clean_cache():
    # The default cache is process-wide and outlives a test; covers cached by
    # one test would otherwise be served to the next.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def library(db, tmp_path):
    # A private copy of the sample book: the ETag keys on the file's mtime, and
    # one test rewrites it.
    shutil.copy(os.path.join(DATA, FB2), tmp_path / FB2)
    config.SOPDS_ROOT_LIB = str(tmp_path)
    # This module is about caching, not access control; the cover routes answer
    # 401 before any of it when SOPDS_AUTH is on (see test_content_auth.py).
    config.SOPDS_AUTH = False
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    return Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(tmp_path / FB2),
        format='fb2', cat_type=0, docdate='2011', lang='en',
        title='The Sanctuary Sparrow', search_title='THE SANCTUARY SPARROW',
        annotation='', avail=2, catalog=cat,
    )


@pytest.mark.django_db
@pytest.mark.parametrize('route', ['opds:cover', 'opds:thumb'])
def test_cover_sets_etag_and_public_cache_control(client, library, route):
    resp = client.get(reverse(route, args=[library.id]))
    assert resp.status_code == 200
    assert resp['ETag']
    assert 'public' in resp['Cache-Control']
    assert 'max-age' in resp['Cache-Control']


@pytest.mark.django_db
@pytest.mark.parametrize('route', ['opds:cover', 'opds:thumb'])
def test_revalidation_returns_304_without_a_body(client, library, route):
    url = reverse(route, args=[library.id])
    etag = client.get(url)['ETag']

    resp = client.get(url, headers={'if-none-match': etag})
    assert resp.status_code == 304
    assert resp.content == b''


@pytest.mark.django_db
def test_cover_and_thumbnail_have_different_etags(client, library):
    cover = client.get(reverse('opds:cover', args=[library.id]))['ETag']
    thumb = client.get(reverse('opds:thumb', args=[library.id]))['ETag']
    assert cover != thumb


@pytest.mark.django_db
def test_etag_changes_when_the_file_is_replaced(client, library, tmp_path):
    url = reverse('opds:cover', args=[library.id])
    before = client.get(url)['ETag']

    # Replaced in place: the scanner keys books on (filename, path) and would
    # not touch the row, so only the on-disk mtime/size can catch this.
    path = tmp_path / FB2
    path.write_bytes(path.read_bytes() + b'<!-- changed -->')

    assert client.get(url)['ETag'] != before


@pytest.mark.django_db
def test_a_replaced_file_is_not_served_from_the_cache(client, library, tmp_path):
    """The regression `cache_page` had: it keyed on the URL, so a book replaced
    in place kept serving its previous cover until the TTL expired."""
    url = reverse('opds:cover', args=[library.id])
    assert client.get(url).status_code == 200          # populates the cache

    # Replace the book with one whose cover we cannot read.
    (tmp_path / FB2).write_bytes(b'not a book')

    resp = client.get(url)
    assert resp.status_code == 200
    with open(config.SOPDS_NOCOVER_PATH, 'rb') as f:
        assert resp.content == f.read()


@pytest.mark.django_db
def test_a_cache_hit_does_not_reopen_the_book(client, library, monkeypatch):
    url = reverse('opds:cover', args=[library.id])
    assert client.get(url).status_code == 200

    def fail(*args, **kwargs):
        raise AssertionError('the book was parsed again on a cache hit')

    monkeypatch.setattr(dl, 'extract_cover', fail)
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_a_coverless_book_is_cached_too(client, library, tmp_path, monkeypatch):
    (tmp_path / FB2).write_bytes(b'not a book')
    url = reverse('opds:cover', args=[library.id])
    assert client.get(url).status_code == 200

    def fail(*args, **kwargs):
        raise AssertionError('a coverless book was parsed again on a cache hit')

    monkeypatch.setattr(dl, 'extract_cover', fail)
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_etag_is_none_for_a_missing_file(client, library, tmp_path):
    os.remove(tmp_path / FB2)
    # No validator to offer, but the placeholder is still served.
    assert dl.cover_etag(None, library.id) is None
    assert client.get(reverse('opds:cover', args=[library.id])).status_code == 200


@pytest.mark.django_db
def test_etag_is_none_for_a_missing_book(library):
    assert dl.cover_etag(None, library.id + 1000) is None


@pytest.mark.django_db
def test_missing_book_is_404_not_a_cached_placeholder(client, library):
    assert client.get(reverse('opds:cover', args=[library.id + 1000])).status_code == 404


@pytest.mark.django_db
def test_covertmpl_serves_the_placeholder(client):
    # Used to be routed at Cover, which needs a book_id, and returned 500.
    resp = client.get(reverse('opds:covertmpl'))
    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/jpeg'
    assert len(resp.content) > 0


@pytest.mark.django_db
def test_cache_timeout_is_read_per_request(client, library):
    config.SOPDS_CACHE_TIME = 77
    resp = client.get(reverse('opds:cover', args=[library.id]))
    assert 'max-age=77' in resp['Cache-Control']
