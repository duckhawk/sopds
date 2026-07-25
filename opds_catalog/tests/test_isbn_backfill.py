"""Coverage for the sopds_isbn_backfill management command."""
import pytest
from django.core.management import call_command

from constance import config

from opds_catalog import opdsdb
from opds_catalog.models import Book, Catalog

FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    '<description>'
    '<title-info><book-title>T</book-title>'
    '<author><last-name>A</last-name><first-name>B</first-name></author>'
    '<lang>ru</lang></title-info>'
    '<publish-info>{isbn}</publish-info>'
    '</description>'
    '<body><section><p>x</p></section></body></FictionBook>'
)


def _book(tmp_path, name, isbn_xml, fmt='fb2'):
    (tmp_path / name).write_bytes(FB2.format(isbn=isbn_xml).encode('utf-8'))
    cat = Catalog.objects.create(cat_name='.', path='.', cat_type=0)
    return Book.objects.create(filename=name, path='.', catalog=cat, format=fmt,
                               title='T', search_title='T', annotation='',
                               docdate='', lang='ru', cat_type=opdsdb.CAT_NORMAL,
                               avail=2, isbn='')


@pytest.mark.django_db
def test_backfill_populates_isbn(tmp_path):
    config.SOPDS_ROOT_LIB = str(tmp_path)
    book = _book(tmp_path, 'a.fb2', '<isbn>978-5-699-12014-7</isbn>')

    call_command('sopds_isbn_backfill')

    book.refresh_from_db()
    assert book.isbn == '9785699120147'


@pytest.mark.django_db
def test_backfill_dry_run_does_not_write(tmp_path):
    config.SOPDS_ROOT_LIB = str(tmp_path)
    book = _book(tmp_path, 'b.fb2', '<isbn>978-5-699-12014-7</isbn>')

    call_command('sopds_isbn_backfill', '--dry-run')

    book.refresh_from_db()
    assert book.isbn == ''


@pytest.mark.django_db
def test_backfill_leaves_book_without_isbn_empty(tmp_path):
    config.SOPDS_ROOT_LIB = str(tmp_path)
    book = _book(tmp_path, 'c.fb2', '')

    call_command('sopds_isbn_backfill')

    book.refresh_from_db()
    assert book.isbn == ''


@pytest.mark.django_db
def test_backfill_skips_already_populated(tmp_path):
    config.SOPDS_ROOT_LIB = str(tmp_path)
    book = _book(tmp_path, 'd.fb2', '<isbn>978-5-699-12014-7</isbn>')
    # A different, already-stored ISBN must not be overwritten (out of queryset).
    Book.objects.filter(pk=book.pk).update(isbn='0306406152')

    call_command('sopds_isbn_backfill')

    book.refresh_from_db()
    assert book.isbn == '0306406152'
