"""Which formats the in-browser reader offers, and what the rest do instead.

FB2 and EPUB render; anything else must 404 rather than feed a binary container
to the XML parser and 500."""
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'
EPUB = 'mirer.epub'


@pytest.fixture
def library(db):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    return Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)


@pytest.fixture
def client(client, django_user_model, db):
    user = django_user_model.objects.create_user(username='reader', password='pw')
    client.force_login(user)
    return client


def make_book(cat, fmt, filename=FB2):
    return Book.objects.create(
        filename=filename, path='.', filesize=1, format=fmt, cat_type=0,
        docdate='2011', lang='en', title='Book in %s' % fmt,
        search_title='BOOK IN %s' % fmt.upper(), annotation='', avail=2, catalog=cat,
    )


@pytest.mark.django_db
@pytest.mark.parametrize('fmt', ['mobi', 'pdf', 'djvu'])
def test_reader_404s_for_formats_it_cannot_render(client, library, fmt):
    """Previously these reached lxml's ET.parse() on a binary container and
    raised, returning 500."""
    book = make_book(library, fmt)
    assert client.get(reverse('opds:read', args=[book.id])).status_code == 404
    assert client.get(reverse('web:read', args=[book.id])).status_code == 404


@pytest.mark.django_db
def test_an_epub_that_is_not_a_zip_404s_too(client, library):
    """Format says epub, contents say otherwise."""
    book = make_book(library, 'epub', filename=FB2)
    assert client.get(reverse('opds:read', args=[book.id])).status_code == 404


@pytest.mark.django_db
def test_reader_still_renders_fb2(client, library):
    book = make_book(library, 'fb2')
    resp = client.get(reverse('opds:read', args=[book.id]))
    assert resp.status_code == 200
    assert resp['Content-Type'].startswith('text/html')
    assert client.get(reverse('web:read', args=[book.id])).status_code == 200


@pytest.mark.django_db
def test_reader_renders_epub(client, library):
    book = make_book(library, 'epub', filename=EPUB)
    resp = client.get(reverse('opds:read', args=[book.id]))
    assert resp.status_code == 200
    assert resp['Content-Type'].startswith('text/html')
    assert client.get(reverse('web:read', args=[book.id])).status_code == 200


@pytest.mark.django_db
def test_book_list_offers_read_for_the_readable_formats_only(client, library):
    make_book(library, 'fb2')
    make_book(library, 'epub', filename=EPUB)
    make_book(library, 'pdf')
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'm', 'searchterms': 'BOOK'}).content.decode()

    for fmt in ('fb2', 'epub'):
        book = Book.objects.get(format=fmt)
        assert reverse('web:read', args=[book.id]) in body, fmt
    pdf = Book.objects.get(format='pdf')
    assert reverse('web:read', args=[pdf.id]) not in body
