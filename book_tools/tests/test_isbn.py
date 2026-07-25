"""ISBN normalisation and per-format extraction (FB2 SAX + lxml, FB2Zip, EPUB)."""
import io
import zipfile

import pytest

from book_tools.format import create_bookfile
from book_tools.format.fb2 import FB2
from book_tools.format.util import normalize_isbn

# 9785699120147 and 0306406152 are valid ISBN-13 / ISBN-10 check digits.
FB2_TEMPLATE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    '<description>'
    '<title-info><book-title>T</book-title>'
    '<author><last-name>A</last-name><first-name>B</first-name></author></title-info>'
    '<publish-info>{isbn}</publish-info>'
    '</description>'
    '<body><section><p>x</p></section></body></FictionBook>'
)


def _fb2_bytes(isbn_xml):
    return FB2_TEMPLATE.format(isbn=isbn_xml).encode('utf-8')


def _make_epub(path, identifier_xml):
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">'
        '<dc:title>Test</dc:title><dc:creator opf:role="aut">Author</dc:creator>'
        '%s'
        '</metadata>'
        '<manifest><item id="t" href="t.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="t"/></spine></package>'
    ) % identifier_xml
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
        '</container>'
    )
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container)
        z.writestr('content.opf', opf)
        z.writestr('t.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>')
    return str(path)


# --- normalisation ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("978-5-699-12014-7", "9785699120147"),
    ("urn:isbn:9785699120147", "9785699120147"),
    ("ISBN 0-306-40615-2", "0306406152"),
    ("0 306 40615 2", "0306406152"),
    ("ISBN: 0306406152", "0306406152"),
    ("978-5-699-12014-1", ""),   # bad ISBN-13 checksum
    ("0-306-40615-3", ""),       # bad ISBN-10 checksum
    ("not-an-isbn", ""),
    ("", ""),
    (None, ""),
    ("urn:uuid:1234", ""),       # a non-ISBN identifier is rejected
])
def test_normalize_isbn(raw, expected):
    assert normalize_isbn(raw) == expected


# --- FB2 -------------------------------------------------------------------

@pytest.mark.django_db
def test_fb2sax_extracts_isbn():
    bf = create_bookfile(io.BytesIO(_fb2_bytes('<isbn>978-5-699-12014-7</isbn>')), 'x.fb2')
    assert type(bf).__name__ == 'FB2sax'
    assert bf.isbn == '9785699120147'


def test_fb2_lxml_extracts_isbn():
    bf = FB2(io.BytesIO(_fb2_bytes('<isbn>978-5-699-12014-7</isbn>')), 'x.fb2')
    assert bf.isbn == '9785699120147'


@pytest.mark.django_db
def test_fb2_without_isbn_is_empty():
    bf = create_bookfile(io.BytesIO(_fb2_bytes('')), 'x.fb2')
    assert bf.isbn == ''


# --- EPUB ------------------------------------------------------------------

@pytest.mark.django_db
def test_epub_extracts_isbn_from_scheme(tmp_path):
    path = _make_epub(tmp_path / 'a.epub',
                      '<dc:identifier id="bookid" opf:scheme="ISBN">978-5-699-12014-7</dc:identifier>')
    bf = create_bookfile(path, 'a.epub')
    assert bf.isbn == '9785699120147'


@pytest.mark.django_db
def test_epub_extracts_isbn_from_urn(tmp_path):
    path = _make_epub(tmp_path / 'b.epub',
                      '<dc:identifier id="bookid">urn:isbn:9785699120147</dc:identifier>')
    bf = create_bookfile(path, 'b.epub')
    assert bf.isbn == '9785699120147'


@pytest.mark.django_db
def test_epub_ignores_non_isbn_identifier(tmp_path):
    path = _make_epub(tmp_path / 'c.epub',
                      '<dc:identifier id="bookid">urn:uuid:deadbeef</dc:identifier>')
    bf = create_bookfile(path, 'c.epub')
    assert bf.isbn == ''
