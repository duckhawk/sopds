"""The kosync digest index is kept current by the scan, not only by hand.

Before this the index only covered the catalogue as it stood when someone last
ran `sopds_kosync_index`. Every book added afterwards was invisible to the
matcher: the sync kept working, it just no longer knew what was being read — and
nothing said so.
"""
import os

import pytest
from constance import config

from opds_catalog.models import Book, Catalog
from sopds_sync import indexing
from sopds_sync.digest import filename_md5
from sopds_sync.models import BookDigest

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'opds_catalog', 'tests', 'data')
FB2 = '262001.fb2'
EPUB = 'mirer.epub'


@pytest.fixture
def catalogue(db):
    config.SOPDS_KOSYNC_ENABLE = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_TITLE_AS_FILENAME = True
    return Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)


def add_book(cat, filename, title):
    return Book.objects.create(
        filename=filename, path='.', filesize=os.path.getsize(os.path.join(DATA, filename)),
        format=filename.rsplit('.', 1)[1], cat_type=0, docdate='2011', lang='en',
        title=title, search_title=title.upper(), annotation='', avail=2, catalog=cat)


@pytest.mark.django_db
def test_new_books_are_indexed(catalogue):
    book = add_book(catalogue, FB2, 'Sparrow')
    stats = indexing.index_new_books()

    assert stats['books'] == 1
    assert BookDigest.objects.filter(book=book).exists()
    assert indexing.pending().count() == 0


@pytest.mark.django_db
def test_a_second_run_costs_nothing(catalogue):
    add_book(catalogue, FB2, 'Sparrow')
    indexing.index_new_books()

    stats = indexing.index_new_books()
    assert stats == {'books': 0, 'added': 0, 'unreadable': 0, 'collisions': 0}


@pytest.mark.django_db
def test_only_the_books_added_since_last_time_are_touched(catalogue):
    """The scan calls this every run; it must not re-hash the whole library."""
    add_book(catalogue, FB2, 'Sparrow')
    indexing.index_new_books()

    add_book(catalogue, EPUB, 'Mirer')
    stats = indexing.index_new_books()
    assert stats['books'] == 1


@pytest.mark.django_db
def test_it_does_nothing_while_kosync_is_disabled(catalogue):
    config.SOPDS_KOSYNC_ENABLE = False
    add_book(catalogue, FB2, 'Sparrow')

    assert indexing.index_new_books() is None
    assert not BookDigest.objects.exists()


@pytest.mark.django_db
def test_an_indexing_failure_cannot_fail_the_scan(catalogue, monkeypatch):
    """This runs at the tail of a scan; the scan has already committed."""
    add_book(catalogue, FB2, 'Sparrow')

    def boom(*args, **kwargs):
        raise RuntimeError('disk went away')

    monkeypatch.setattr(indexing, 'index', boom)
    assert indexing.index_new_books() is None


@pytest.mark.django_db
def test_an_unreadable_book_is_counted_not_fatal(catalogue):
    Book.objects.create(
        filename='gone.fb2', path='.', filesize=1, format='fb2', cat_type=0,
        docdate='2011', lang='en', title='Gone', search_title='GONE',
        annotation='', avail=2, catalog=catalogue)
    stats = indexing.index_new_books()

    assert stats['unreadable'] == 1
    # The name digests still land, so a rename-based match keeps working.
    assert BookDigest.objects.filter(method=BookDigest.FILENAME).exists()


@pytest.mark.django_db
def test_the_digests_are_the_ones_the_matcher_looks_up(catalogue):
    book = add_book(catalogue, FB2, 'Sparrow')
    indexing.index_new_books()

    from sopds_sync import linking
    assert linking.resolve_book(filename_md5(FB2)) == book
