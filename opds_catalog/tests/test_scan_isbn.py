"""End-to-end: the scanner extracts a book's ISBN and stores it on Book."""
import pytest

from constance import config

from opds_catalog import opdsdb
from opds_catalog.models import Book
from opds_catalog.sopdscan import opdsScanner

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


def _write(lib, name, isbn_xml):
    data = FB2.format(isbn=isbn_xml).encode('utf-8')
    p = lib / name
    p.write_bytes(data)
    return p


@pytest.mark.django_db
def test_processfile_stores_isbn(tmp_path):
    opdsdb.clear_all()
    config.SOPDS_ROOT_LIB = str(tmp_path)
    p = _write(tmp_path, 'withisbn.fb2', '<isbn>978-5-699-12014-7</isbn>')

    scanner = opdsScanner()
    scanner.processfile('withisbn.fb2', str(tmp_path), str(p), None, 0, p.stat().st_size)

    assert Book.objects.get(filename='withisbn.fb2').isbn == '9785699120147'


@pytest.mark.django_db
def test_processfile_without_isbn_stores_empty(tmp_path):
    opdsdb.clear_all()
    config.SOPDS_ROOT_LIB = str(tmp_path)
    p = _write(tmp_path, 'noisbn.fb2', '')

    scanner = opdsScanner()
    scanner.processfile('noisbn.fb2', str(tmp_path), str(p), None, 0, p.stat().st_size)

    assert Book.objects.get(filename='noisbn.fb2').isbn == ''
