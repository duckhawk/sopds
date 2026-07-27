"""What a book is called when the file says nothing.

A PDF or a DjVu scan carries no metadata the scanner can read, so the filename
is all there is. The extension is not part of the title.
"""
import io

import pytest

from book_tools.format.bookfile import BookFile
from book_tools.format.mimetype import Mimetype


class Plain(BookFile):
    """The base class is abstract only in its teardown."""

    def __exit__(self, kind, value, traceback):
        pass


def title_of(filename):
    return Plain(io.BytesIO(b''), filename, Mimetype.PDF).title


@pytest.mark.parametrize('filename, expected', [
    ('Shuty i skomorokhi.djvu', 'Shuty i skomorokhi'),
    ('Аквилонов. Не убий (1906).pdf', 'Аквилонов. Не убий (1906)'),
    ('report.final.pdf', 'report.final'),
])
def test_the_extension_is_not_part_of_the_title(filename, expected):
    assert title_of(filename) == expected


def test_a_name_that_is_only_an_extension_is_kept_whole():
    """Better an odd title than an empty one."""
    assert title_of('.pdf') == '.pdf'


def test_a_name_without_an_extension_is_unchanged():
    assert title_of('untitled') == 'untitled'


def test_metadata_still_wins():
    book = Plain(io.BytesIO(b''), 'whatever.pdf', Mimetype.PDF)
    book.__set_title__('The Real Title')
    assert book.title == 'The Real Title'
