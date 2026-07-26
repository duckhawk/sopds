"""Caching and conditional GETs for the EPUB reader.

Rendering a book means unzipping it and parsing every document in its spine, and
the page then asks for each illustration separately — each of which used to read
the whole archive again.
"""
import os
import shutil

import pytest
from django.core.cache import cache
from django.urls import reverse
from constance import config

from opds_catalog import dl, epub_render
from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
EPUB = 'mirer.epub'
IMAGE = 'OEBPS/images/MIRERUmenjadevjatzhiznejj.jpg'


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def book(db, tmp_path, django_user_model, client):
    # A private copy: the validator keys on mtime and one test rewrites it.
    shutil.copy(os.path.join(DATA, EPUB), tmp_path / EPUB)
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = str(tmp_path)
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    book = Book.objects.create(
        filename=EPUB, path='.', filesize=os.path.getsize(tmp_path / EPUB),
        format='epub', cat_type=0, docdate='2011', lang='ru', title='Mirer',
        search_title='MIRER', annotation='', avail=2, catalog=cat)
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    return book


def res_url(book, path=IMAGE):
    return reverse('opds:readres', kwargs={'book_id': book.id, 'path': path})


# --- the rendered text -----------------------------------------------------

@pytest.mark.django_db
def test_the_render_is_cached(client, book, monkeypatch):
    url = reverse('opds:read', args=[book.id])
    first = client.get(url)
    assert first.status_code == 200

    def fail(*args, **kwargs):
        raise AssertionError('the book was parsed again on a cache hit')

    monkeypatch.setattr(epub_render, 'render_archive', fail)
    second = client.get(url)
    assert second.status_code == 200
    assert second.content == first.content


@pytest.mark.django_db
def test_revalidation_returns_304(client, book):
    url = reverse('opds:read', args=[book.id])
    etag = client.get(url)['ETag']

    resp = client.get(url, headers={'if-none-match': etag})
    assert resp.status_code == 304
    assert resp.content == b''


@pytest.mark.django_db
def test_replacing_the_file_invalidates_the_render(client, book, tmp_path):
    url = reverse('opds:read', args=[book.id])
    before = client.get(url)
    assert before.status_code == 200

    (tmp_path / EPUB).write_bytes(b'not a book any more')
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_the_reader_page_is_privately_cacheable(client, book):
    """It is behind authentication, so no shared proxy may keep it."""
    resp = client.get(reverse('opds:read', args=[book.id]))
    assert 'private' in resp['Cache-Control']


# --- illustrations ---------------------------------------------------------

@pytest.mark.django_db
def test_an_image_is_cached(client, book, monkeypatch):
    assert client.get(res_url(book)).status_code == 200

    def fail(*args, **kwargs):
        raise AssertionError('the archive was reopened on a cache hit')

    monkeypatch.setattr(dl, 'open_book_archive', fail)
    resp = client.get(res_url(book))
    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/jpeg'


@pytest.mark.django_db
def test_an_image_revalidates_to_304(client, book):
    etag = client.get(res_url(book))['ETag']
    resp = client.get(res_url(book), headers={'if-none-match': etag})
    assert resp.status_code == 304


@pytest.mark.django_db
def test_text_and_image_do_not_share_a_cache_entry(client, book):
    text = client.get(reverse('opds:read', args=[book.id]))
    image = client.get(res_url(book))
    assert text['ETag'] != image['ETag']
    assert image['Content-Type'] == 'image/jpeg'


@pytest.mark.django_db
def test_two_images_do_not_share_a_cache_entry(client, book):
    """The validator has to include the member path, not just the book."""
    one = dl.resource_etag(None, book.id, IMAGE)
    two = dl.resource_etag(None, book.id, 'OEBPS/images/other.jpg')
    assert one != two


@pytest.mark.django_db
def test_a_refused_path_is_still_refused_when_cached(client, book):
    """Rejection happens before the cache, so a 404 cannot be poisoned into a 200."""
    for _ in range(2):
        assert client.get(res_url(book, 'OEBPS/content.opf')).status_code == 404
        assert client.get(res_url(book, '../../../etc/passwd')).status_code == 404


@pytest.mark.django_db
def test_a_plain_epub_is_read_without_slurping_the_whole_file(book, monkeypatch):
    """`getFileData` reads the entire book into memory; for a book that *is* the
    archive there is no reason to."""
    def fail(*args, **kwargs):
        raise AssertionError('the whole archive was read into memory')

    monkeypatch.setattr(dl, 'getFileData', fail)
    with dl.open_book_archive(book) as archive:
        assert IMAGE in archive.namelist()
