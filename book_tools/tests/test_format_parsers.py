"""Parser coverage for book_tools.format using the real sample files in
opds_catalog/tests/data (fb2, epub, mobi)."""
import os

import pytest

from book_tools.format import create_bookfile, mime_detector
from book_tools.format.mimetype import Mimetype
from book_tools.format.epub import EPub
from book_tools.format.mobi import Mobipocket

DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'opds_catalog', 'tests', 'data',
)


def test_mime_detector_by_extension():
    assert mime_detector.file('x.fb2') == Mimetype.FB2
    assert mime_detector.file('x.epub') == Mimetype.EPUB
    assert mime_detector.file('x.mobi') == Mimetype.MOBI
    assert mime_detector.file('x.pdf') == Mimetype.PDF
    assert mime_detector.file('x.whatever') == Mimetype.OCTET_STREAM


@pytest.mark.django_db
def test_fb2_parser_reads_metadata():
    bf = create_bookfile(os.path.join(DATA, '262001.fb2'), '262001.fb2')
    assert bf.title == 'The Sanctuary Sparrow'
    assert len(bf.authors) >= 1


@pytest.mark.django_db
def test_epub_parser():
    bf = create_bookfile(os.path.join(DATA, 'mirer.epub'), 'mirer.epub')
    assert isinstance(bf, EPub)
    assert bf.title


@pytest.mark.django_db
def test_mobi_parser():
    bf = create_bookfile(os.path.join(DATA, 'robin_cook.mobi'), 'robin_cook.mobi')
    assert isinstance(bf, Mobipocket)
    assert bf.title


@pytest.mark.django_db
def test_badfile_is_not_recognised_as_fb2():
    # A tiny non-FB2 file with an .fb2 name must not be parsed as FB2.
    with pytest.raises(Exception):
        create_bookfile(os.path.join(DATA, 'badfile.fb2'), 'badfile.fb2')
