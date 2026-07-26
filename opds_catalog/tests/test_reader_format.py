"""Which formats the in-browser reader offers, and what the rest do instead.

FB2 and EPUB flow through `opds:read`; anything else must 404 there rather than
feed a binary container to the XML parser and 500. PDF and DjVu are shown by
the paged reader instead — see test_paged_reader.py — and DjVu only where a
converter is installed, which is turned off here so the answers do not depend
on what the host happens to have."""
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog import paged
from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'
EPUB = 'mirer.epub'


@pytest.fixture
def library(db):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_DJVUTOPDF = ''
    paged._converter_argv.cache_clear()
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
def test_the_flowing_renderer_404s_for_everything_else(client, library, fmt):
    """Previously these reached lxml's ET.parse() on a binary container and
    raised, returning 500."""
    book = make_book(library, fmt)
    assert client.get(reverse('opds:read', args=[book.id])).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('fmt', ['mobi', 'djvu'])
def test_no_reader_at_all_for_a_format_neither_can_show(client, library, fmt):
    book = make_book(library, fmt)
    assert client.get(reverse('web:read', args=[book.id])).status_code == 404


@pytest.mark.django_db
def test_a_pdf_opens_in_the_paged_reader(client, library):
    book = make_book(library, 'pdf', filename='scan.pdf')
    body = client.get(reverse('web:read', args=[book.id])).content.decode()
    assert reverse('opds:pdfsource', args=[book.id]) in body


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
def test_book_list_offers_read_for_the_showable_formats_only(client, library):
    make_book(library, 'fb2')
    make_book(library, 'epub', filename=EPUB)
    make_book(library, 'pdf', filename='scan.pdf')
    make_book(library, 'djvu', filename='scan.djvu')    # no converter here
    make_book(library, 'mobi')
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'm', 'searchterms': 'BOOK'}).content.decode()

    for fmt in ('fb2', 'epub', 'pdf'):
        book = Book.objects.get(format=fmt)
        assert reverse('web:read', args=[book.id]) in body, fmt
    for fmt in ('djvu', 'mobi'):
        book = Book.objects.get(format=fmt)
        assert reverse('web:read', args=[book.id]) not in body, fmt
