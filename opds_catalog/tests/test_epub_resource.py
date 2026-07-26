"""Serving illustrations out of an EPUB for the rendered reader page.

The path in the URL comes from the book's own markup, so it is untrusted: the
view must only ever hand back something the archive actually contains, and only
if it is an image.
"""
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
EPUB = 'mirer.epub'
IMAGE = 'OEBPS/images/MIRERUmenjadevjatzhiznejj.jpg'


@pytest.fixture
def book(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    book = Book.objects.create(
        filename=EPUB, path='.', filesize=os.path.getsize(os.path.join(DATA, EPUB)),
        format='epub', cat_type=0, docdate='2011', lang='ru', title='Mirer',
        search_title='MIRER', annotation='', avail=2, catalog=cat)
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    return book


def url(book, path):
    return reverse('opds:readres', kwargs={'book_id': book.id, 'path': path})


@pytest.mark.django_db
def test_serves_an_image_the_archive_contains(client, book):
    resp = client.get(url(book, IMAGE))
    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/jpeg'
    assert len(resp.content) > 0


@pytest.mark.django_db
def test_the_rendered_page_links_images_that_resolve(client, book):
    body = client.get(reverse('opds:read', args=[book.id])).content.decode()
    assert url(book, IMAGE) in body


@pytest.mark.django_db
@pytest.mark.parametrize('path', [
    '../../../../etc/passwd',
    'OEBPS/../../../../etc/passwd',
    '/etc/passwd',
    'OEBPS/images/../../../secret',
])
def test_traversal_is_refused(client, book, path):
    assert client.get(url(book, path)).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('path', [
    'OEBPS/content.opf',                       # in the archive, but not an image
    'OEBPS/css/main.css',
    'OEBPS/fonts/liberationserif-regular.ttf',
    'mimetype',
])
def test_non_images_inside_the_archive_are_refused(client, book, path):
    """Membership alone is not enough: the reader page only needs pictures."""
    assert client.get(url(book, path)).status_code == 404


@pytest.mark.django_db
def test_a_missing_member_is_404(client, book):
    assert client.get(url(book, 'OEBPS/images/nope.png')).status_code == 404


@pytest.mark.django_db
def test_resources_require_authentication(client, book):
    client.logout()
    assert client.get(url(book, IMAGE)).status_code == 401


@pytest.mark.django_db
def test_a_non_epub_book_has_no_resources(client, book):
    book.format = 'fb2'
    book.save(update_fields=['format'])
    assert client.get(url(book, IMAGE)).status_code == 404


@pytest.mark.django_db
def test_the_response_is_privately_cacheable(client, book):
    """A cover can be shared, but a page out of a book is behind auth."""
    resp = client.get(url(book, IMAGE))
    assert 'private' in resp['Cache-Control']
