"""Reading a PDF or a DjVu in the browser.

These are pages of ink at fixed coordinates, not text that reflows, so they get
their own reader and their own content route: the book as a PDF, converted
first if it is a DjVu.

The DjVu conversion is exercised through a stub standing in for `ddjvu`, so the
plumbing — how the command is split, what happens when it fails or produces
nothing, what is cached and for how long — is tested everywhere, and the one
test that needs djvulibre itself is skipped where it is not installed.
"""
import os
import shutil
import sys
import zipfile

import pytest
from django.urls import reverse
from constance import config

from opds_catalog import opdsdb, paged
from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PDF = 'scan.pdf'
DJVU = 'scan.djvu'
PDF_BYTES = open(os.path.join(DATA, PDF), 'rb').read()


@pytest.fixture
def library(db, tmp_path):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_TEMP_DIR = str(tmp_path / 'tmp')
    config.SOPDS_RATE_LIMIT = 0
    config.SOPDS_DJVUTOPDF = ''
    paged._converter_argv.cache_clear()
    return Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)


@pytest.fixture
def client(client, django_user_model, db):
    client.force_login(django_user_model.objects.create_user(username='reader', password='pw'))
    return client


def make_book(cat, fmt=None, filename=PDF, path='.', cat_type=opdsdb.CAT_NORMAL):
    fmt = fmt or os.path.splitext(filename)[1][1:]
    return Book.objects.create(
        filename=filename, path=path, filesize=1, format=fmt, cat_type=cat_type,
        docdate='2011', lang='en', title='Scan in %s' % fmt,
        search_title='SCAN IN %s' % fmt.upper(), annotation='', avail=2, catalog=cat)


def stub_converter(tmp_path, script):
    """Point SOPDS_DJVUTOPDF at a Python stub standing in for ddjvu.

    The stub is given the source and destination paths the real converter would
    get, so everything around the call is the same.
    """
    path = tmp_path / 'stub.py'
    path.write_text(script)
    config.SOPDS_DJVUTOPDF = '%s %s' % (sys.executable, path)
    paged._converter_argv.cache_clear()
    return path


COPIES_A_PDF = (
    'import shutil, sys\n'
    'shutil.copyfile(%r, sys.argv[-1])\n' % os.path.join(DATA, PDF)
)


def source_url(book):
    return reverse('opds:pdfsource', args=[book.id])


# --- a PDF, which needs no converting --------------------------------------

@pytest.mark.django_db
def test_a_pdf_is_served_as_it_is(client, library):
    book = make_book(library)
    resp = client.get(source_url(book))

    assert resp.status_code == 200
    assert resp['Content-Type'] == 'application/pdf'
    assert b''.join(resp.streaming_content) == PDF_BYTES


@pytest.mark.django_db
def test_a_pdf_is_never_copied_into_the_cache(client, library):
    """It is already the thing the reader wants; the collection is the cache."""
    book = make_book(library)
    client.get(source_url(book))
    assert not os.path.exists(paged.cache_dir())


@pytest.mark.django_db
def test_the_reader_page_opens_for_a_pdf(client, library):
    book = make_book(library)
    body = client.get(reverse('web:read', args=[book.id])).content.decode()

    assert source_url(book) in body
    assert 'pdf.min.mjs' in body


@pytest.mark.django_db
def test_the_flowing_reader_is_not_used_for_a_pdf(client, library):
    """It would hand a binary container to the XML parser."""
    book = make_book(library)
    assert client.get(reverse('opds:read', args=[book.id])).status_code == 404


@pytest.mark.django_db
def test_a_pdf_inside_a_zip_is_unpacked(client, library, tmp_path):
    config.SOPDS_ROOT_LIB = str(tmp_path)
    archive = tmp_path / 'scans.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.write(os.path.join(DATA, PDF), PDF)

    book = make_book(library, filename=PDF, path='scans.zip', cat_type=opdsdb.CAT_ZIP)
    resp = client.get(source_url(book))

    assert resp.status_code == 200
    assert b''.join(resp.streaming_content) == PDF_BYTES
    assert len(os.listdir(paged.cache_dir())) == 1


@pytest.mark.django_db
def test_a_missing_file_is_a_404_not_a_crash(client, library):
    book = make_book(library, filename='not-here.pdf')
    assert client.get(source_url(book)).status_code == 404


# --- Range, which is what makes a large scan usable -------------------------

@pytest.mark.django_db
def test_the_whole_file_advertises_that_ranges_are_accepted(client, library):
    resp = client.get(source_url(make_book(library)))
    assert resp['Accept-Ranges'] == 'bytes'
    assert resp['Content-Length'] == str(len(PDF_BYTES))


@pytest.mark.django_db
def test_a_range_is_answered_with_that_range(client, library):
    resp = client.get(source_url(make_book(library)), HTTP_RANGE='bytes=100-199')

    assert resp.status_code == 206
    assert resp['Content-Range'] == 'bytes 100-199/%d' % len(PDF_BYTES)
    assert resp['Content-Length'] == '100'
    assert b''.join(resp.streaming_content) == PDF_BYTES[100:200]


@pytest.mark.django_db
def test_an_open_ended_range_runs_to_the_end(client, library):
    resp = client.get(source_url(make_book(library)), HTTP_RANGE='bytes=3900-')

    assert resp.status_code == 206
    assert b''.join(resp.streaming_content) == PDF_BYTES[3900:]


@pytest.mark.django_db
def test_a_suffix_range_is_the_last_bytes(client, library):
    """pdf.js opens a document by reading its trailer, which is at the end."""
    resp = client.get(source_url(make_book(library)), HTTP_RANGE='bytes=-64')

    assert resp.status_code == 206
    assert b''.join(resp.streaming_content) == PDF_BYTES[-64:]


@pytest.mark.django_db
def test_a_range_past_the_end_is_refused(client, library):
    resp = client.get(source_url(make_book(library)), HTTP_RANGE='bytes=99999-')

    assert resp.status_code == 416
    assert resp['Content-Range'] == 'bytes */%d' % len(PDF_BYTES)


@pytest.mark.django_db
def test_a_range_header_that_makes_no_sense_is_ignored(client, library):
    """Better the whole book than an error the reader cannot recover from."""
    resp = client.get(source_url(make_book(library)), HTTP_RANGE='pages=1-2')

    assert resp.status_code == 200
    assert b''.join(resp.streaming_content) == PDF_BYTES


# --- revalidation -----------------------------------------------------------

@pytest.mark.django_db
def test_an_unchanged_book_revalidates_as_304(client, library):
    book = make_book(library)
    tag = client.get(source_url(book))['ETag']

    assert client.get(source_url(book), HTTP_IF_NONE_MATCH=tag).status_code == 304


# --- DjVu, which has to be converted ----------------------------------------

@pytest.mark.django_db
def test_djvu_is_not_offered_without_a_converter(client, library):
    """A reader that can never load is worse than no link at all."""
    book = make_book(library, filename=DJVU)

    assert paged.viewable_formats() == ('pdf',)
    assert client.get(source_url(book)).status_code == 404
    assert client.get(reverse('web:read', args=[book.id])).status_code == 404


@pytest.mark.django_db
def test_djvu_is_converted_and_served_as_a_pdf(client, library, tmp_path):
    stub_converter(tmp_path, COPIES_A_PDF)
    book = make_book(library, filename=DJVU)

    resp = client.get(source_url(book))
    assert resp.status_code == 200
    assert resp['Content-Type'] == 'application/pdf'
    assert b''.join(resp.streaming_content) == PDF_BYTES


@pytest.mark.django_db
def test_the_conversion_happens_once(client, library, tmp_path):
    counter = tmp_path / 'runs'
    stub_converter(tmp_path,
                   'import shutil, sys\n'
                   'open(%r, "a").write("x")\n'
                   'shutil.copyfile(%r, sys.argv[-1])\n'
                   % (str(counter), os.path.join(DATA, PDF)))
    book = make_book(library, filename=DJVU)

    for _ in range(3):
        assert client.get(source_url(book)).status_code == 200
    assert counter.read_text() == 'x'


@pytest.mark.django_db
def test_the_download_offers_the_djvu_itself_not_the_conversion(client, library, tmp_path):
    """Converting is for reading here. A reader taking the book away wants it."""
    stub_converter(tmp_path, COPIES_A_PDF)
    book = make_book(library, filename=DJVU)

    resp = client.get(reverse('opds:download', args=[book.id, 0]))
    assert resp.status_code == 200
    assert resp.content[:4] == b'AT&T'          # the DjVu magic


@pytest.mark.django_db
def test_a_converter_that_fails_is_a_404(client, library, tmp_path):
    stub_converter(tmp_path, 'import sys\nsys.exit(1)\n')
    assert client.get(source_url(make_book(library, filename=DJVU))).status_code == 404


@pytest.mark.django_db
def test_a_converter_that_produces_nothing_is_a_404(client, library, tmp_path):
    """ddjvu is content with an unreadable page and still exits 0."""
    stub_converter(tmp_path, 'pass\n')
    assert client.get(source_url(make_book(library, filename=DJVU))).status_code == 404


@pytest.mark.django_db
def test_an_empty_conversion_is_not_cached(client, library, tmp_path):
    """Or the failure would be served as the book from then on."""
    stub_converter(tmp_path, 'import sys\nopen(sys.argv[-1], "wb").close()\n')
    assert client.get(source_url(make_book(library, filename=DJVU))).status_code == 404
    assert os.listdir(paged.cache_dir()) == []


@pytest.mark.django_db
def test_a_converter_that_is_not_installed_is_not_run(client, library):
    config.SOPDS_DJVUTOPDF = '/nowhere/ddjvu -format=pdf'
    paged._converter_argv.cache_clear()

    assert paged.djvu_converter() is None
    assert paged.viewable_formats() == ('pdf',)


@pytest.mark.django_db
def test_replacing_the_file_invalidates_the_conversion(client, library, tmp_path):
    """The cache is keyed on the same validator as the ETag, which is the size
    and mtime of the file on disk — the scanner does not refresh the row."""
    config.SOPDS_ROOT_LIB = str(tmp_path)
    source = tmp_path / DJVU
    shutil.copyfile(os.path.join(DATA, DJVU), source)
    stub_converter(tmp_path, COPIES_A_PDF)
    book = make_book(library, filename=DJVU)

    first = client.get(source_url(book))['ETag']
    os.utime(source, (0, 0))
    second = client.get(source_url(book))['ETag']

    assert first != second
    assert len(os.listdir(paged.cache_dir())) == 2


# --- keeping the cache from growing without limit ---------------------------

def test_the_cache_is_pruned_oldest_first(tmp_path):
    directory = tmp_path / 'paged'
    directory.mkdir()
    for n, age in enumerate([300, 100, 200]):
        f = directory / ('%d.pdf' % n)
        f.write_bytes(b'x' * 1000)
        os.utime(f, (age, age))

    paged._prune(str(directory), limit=1500)

    # 1000 bytes each, room for one: the two older ones go.
    assert os.listdir(directory) == ['0.pdf']


def test_pruning_an_unwritable_cache_does_not_raise(tmp_path):
    paged._prune(str(tmp_path / 'not-there'))


# --- access -----------------------------------------------------------------

@pytest.mark.django_db
def test_the_source_needs_authentication(db, library):
    from django.test import Client
    assert Client().get(source_url(make_book(library))).status_code == 401


@pytest.mark.django_db
def test_the_source_is_rate_limited(client, library):
    """It is content, and converting it is expensive."""
    config.SOPDS_RATE_LIMIT = 2
    book = make_book(library)

    codes = [client.get(source_url(book)).status_code for _ in range(4)]
    assert 429 in codes


@pytest.mark.django_db
def test_a_format_that_is_not_paged_is_a_404(client, library):
    book = make_book(library, fmt='mobi', filename='robin_cook.mobi')
    assert client.get(source_url(book)).status_code == 404


# --- the real thing ---------------------------------------------------------

@pytest.mark.skipif(shutil.which('ddjvu') is None, reason='djvulibre is not installed')
@pytest.mark.django_db
def test_djvulibre_produces_a_pdf_this_reader_can_show(client, library):
    """The default command, against a real DjVu, on a host that has djvulibre."""
    config.SOPDS_DJVUTOPDF = 'ddjvu -format=pdf -quality=75 -skip'
    paged._converter_argv.cache_clear()
    book = make_book(library, filename=DJVU)

    resp = client.get(source_url(book))
    assert resp.status_code == 200

    body = b''.join(resp.streaming_content)
    assert body[:5] == b'%PDF-'
    assert b'/Type /Page' in body


def test_the_default_command_is_the_one_that_was_measured():
    """-quality keeps a photographic scan from converting to a gigabyte of
    lossless raster, and -skip leaves a damaged page blank instead of losing
    the book. Losing either silently is the kind of change this catches."""
    from sopds.settings import CONSTANCE_CONFIG
    command = CONSTANCE_CONFIG['SOPDS_DJVUTOPDF'][0]
    assert '-quality=' in command and '-skip' in command


@pytest.mark.django_db
def test_opening_a_book_puts_it_on_the_shelf(client, library):
    from opds_catalog.models import bookshelf
    book = make_book(library)
    client.get(source_url(book))
    assert bookshelf.objects.filter(book=book).exists()


@pytest.mark.django_db
def test_a_range_request_does_not_touch_the_shelf(client, library, django_assert_num_queries):
    """Reading a scan is hundreds of them; each would cost a query to
    rediscover that the book is already there."""
    from opds_catalog.models import bookshelf
    book = make_book(library)
    bookshelf.objects.all().delete()

    client.get(source_url(book), HTTP_RANGE='bytes=0-99')
    assert not bookshelf.objects.exists()
